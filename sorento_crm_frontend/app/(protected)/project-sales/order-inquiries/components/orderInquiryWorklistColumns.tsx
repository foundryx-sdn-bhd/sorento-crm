'use client';

import * as React from 'react';
import Link from 'next/link';
import { ColumnDef, Table } from '@tanstack/react-table';
import { CircleCheck, CircleDashed, Info } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { DataGridColumnHeader } from '@/components/ui/data-grid-column-header';
import { buildSelectColumn } from '@/components/ui/data-grid-select-column';
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Skeleton } from '@/components/ui/skeleton';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import { formatDateInMalaysia, formatDateTimeInMalaysia } from '@/lib/helpers';
import {
  ackStateOf,
  bundledHostChangeLines,
  movedNoteOf,
  previousValueOf,
} from '../../_shared/lib/orderInquiryAck';
import { DecisionTrailButton } from '../../_shared/components/DecisionTrailButton';
import { OrderInquiryVerbPill, ReservePill } from '../../_shared/components/OrderInquiryVerbPill';
import {
  bundledHeadline,
  formatInquiryQty,
  inquiryFooterTotals,
  inquiryRowRemaining,
  inquiryRowTaken,
  orderInquiryRowHref,
  orderInquirySoLineHref,
  orderInquirySoLineLabel,
  raisedKindLabel,
} from '../../_shared/lib/orderInquiryWorklist';
import type {
  OrderInquiryLinkSuggestion,
  OrderInquiryWorklistRow,
} from '../../_shared/types/orderInquiry.types';
import { OrderInquiryBackingDocumentsDialog } from './OrderInquiryBackingDocumentsDialog';
import { OrderInquiryDocumentLink } from './OrderInquiryDocumentDialog';
import { OrderInquiryQtyAnnotationDialog } from './OrderInquiryQtyAnnotationDialog';

function Muted({ children }: { children: React.ReactNode }) {
  return <span className="text-muted-foreground">{children}</span>;
}

/**
 * S6 (AC-OH-01): columns hidden on first load, before a saved column preference (if any)
 * applies and wins. The order inquiry number stays on the header, the email and the URL -
 * purchasing does not need it as a worklist column any more.
 */
export const DEFAULT_HIDDEN_COLUMNS: string[] = ['inquiry_no', 'raise_event'];

/**
 * REV design (17 Sep review round): the row's own one-word marks - `via PO`/`via SPO`,
 * `received`, `reallocate`/`unlink`, `used`, `note` - shared ONE pill from here on,
 * rather than four hand-rolled spellings of `text-2xs text-muted-foreground` (one of
 * them a literal `text-[var(--color-warning-accent,...)]` colour). The same `Badge`
 * idiom the `+N` pill beside them already used (`size="sm" appearance="light" asChild`);
 * `warning` is the design system's own token for the amber marks, never a literal one.
 */
export function WorklistPill({
  as = 'span',
  warning = false,
  testId,
  onClick,
  ariaLabel,
  children,
}: {
  as?: 'span' | 'button';
  warning?: boolean;
  testId: string;
  onClick?: (event: React.MouseEvent) => void;
  ariaLabel?: string;
  children: React.ReactNode;
}) {
  return (
    <Badge asChild size="sm" variant={warning ? 'warning' : 'secondary'} appearance="light">
      {as === 'button' ? (
        <button
          type="button"
          data-testid={testId}
          aria-label={ariaLabel}
          className="shrink-0"
          onClick={onClick}
        >
          {children}
        </button>
      ) : (
        <span data-testid={testId} className="shrink-0">
          {children}
        </span>
      )}
    </Badge>
  );
}

/**
 * Is what this row shows firm, or still a row nobody has acknowledged (S1: essentially
 * never, since a row is born acknowledged - this reads a legacy `awaiting`/`changed` row
 * only, kept honest rather than assumed away)?
 *
 * There is no state on the link: a link is firm the moment its row reads `acknowledged`.
 * One source of truth, read two ways, so this mark can never disagree with the row itself.
 */
function DraftMark({ row }: { row: OrderInquiryWorklistRow }) {
  const state = ackStateOf(row);
  // A refused row's links are gone; there is nothing to mark as anything.
  if (state === 'rejected') return null;
  if (state === 'acknowledged') {
    const who = row.acknowledged_by_name ?? 'Purchasing';
    const when = row.acknowledged_at ? ` ${formatDateInMalaysia(row.acknowledged_at)}` : '';
    const label = `Confirmed by ${who}${when}`;
    return (
      <span data-testid="link-confirmed-mark" title={label} aria-label={label}>
        <CircleCheck className="size-3.5 shrink-0 text-emerald-600" aria-hidden />
      </span>
    );
  }
  return (
    <span data-testid="link-draft-mark" title="Proposed" aria-label="Proposed">
      <CircleDashed className="size-3.5 shrink-0 text-muted-foreground" aria-hidden />
    </span>
  );
}

/**
 * The distinct document NUMBERS this row is linked to, of one book, in link order.
 *
 * Distinct numbers rather than links: two containers of one shipping order are one
 * document to the person reading the list (AC-R-27), and the `+N` pill counts documents,
 * not placements. The lightbox behind the number still lists every link.
 *
 * The PO side also answers with the purchase order a SHIPMENT came from (7.2, owner 14 Sep
 * evening: "we definitely cannot double count, but by this linking it helps us to know the
 * PO and SPO corresponding to this order inquiry"). The importer stopped linking a purchase
 * order line for units already on its own ship, so a row whose whole quantity has sailed
 * holds no `po` link at all, and this column would go blank on exactly the rows purchasing
 * most wants to trace. A purchase order and its own shipment are ONE number here: the
 * partly shipped row carries both a `po` link and an `spo` link naming the same purchase
 * order, and the list must not read that as two.
 */
/**
 * One document number for this cell's book, and whether it is DERIVED (S5, R-E) - the
 * PO column's number read off an SPO link's `source_po_number` (`via: 'spo'`), or the
 * SPO column's number that is really the linked PO's own open shipment (`via: 'po'`).
 * Written nowhere: no link is created for either, so `via` never appears outside these
 * two columns.
 */
export interface DocumentEntry {
  document: string;
  via: 'po' | 'spo' | null;
  /**
   * R17 (owner rulings, 25 Sep 2026): addresses the PO lightbox for a `kind: 'po'`
   * entry - the real link's own `po_id`, or, for a `via: 'spo'` derived entry, the
   * source PO id resolved through the SPO allocation's own supply PO line
   * (`purchase_order_id`). Null when neither resolves (an SPO allocation with no
   * supply PO line, whose PO the Lines tab / worklist then look up by number
   * instead). Never set for a `kind: 'spo'` entry - there is no PO to address there.
   */
  poId: string | null;
  /**
   * The FIRST link naming this document is fully received (S1,
   * `PLAN-oi-replan-received-links.md`, AC-RL-02) - goods that have landed, not a promise
   * still in transit. `receivedQty`/`qty` back the chip's own title; both are the LINK's
   * own figures, never the row's.
   */
  received: boolean;
  receivedQty: string | null;
  qty: string | null;
  /** The link's own promised arrival - the reallocate lightbox states it beside the
   * row's own delivery date (AC-RL-24). */
  expectedDate: string | null;
  /**
   * S1b (`PLAN-oi-replan-received-links.md`, AC-RL-20 to AC-RL-24): the FIRST link
   * naming this document carries a reallocate/unlink instruction. Never on a received
   * link (S1's own `received` and S1b's `suggestion` are mutually exclusive by
   * construction on the wire, but the chip reads whichever the link states).
   */
  suggestion: OrderInquiryLinkSuggestion | null;
}

export function documentsOf(row: OrderInquiryWorklistRow, kind: 'po' | 'spo'): DocumentEntry[] {
  const entries: DocumentEntry[] = [];
  const seen = new Set<string>();
  for (const link of row.links ?? []) {
    let named: string | null = null;
    let via: 'po' | 'spo' | null = null;
    let poId: string | null = null;
    if (kind === 'po') {
      if (link.kind === 'po') {
        named = link.document;
        poId = link.po_id ?? null;
      } else if (link.kind === 'spo' && link.source_po_number) {
        named = link.source_po_number;
        via = link.derived_po ? 'spo' : null;
        // R17: the source PO's OWN id, resolved through this SPO allocation's supply
        // PO line - never guessed here, the Lines tab / worklist fall back to a
        // number lookup only when this is null.
        poId = link.purchase_order_id ?? null;
      }
    } else if (link.kind === 'spo') {
      named = link.document;
      via = link.derived ? 'po' : null;
    }
    const document = (named ?? '').trim();
    if (!document || seen.has(document)) continue;
    seen.add(document);
    entries.push({
      document,
      via,
      poId,
      received: Boolean(link.received),
      receivedQty: link.received_qty ?? null,
      qty: link.qty ?? null,
      expectedDate: link.expected_date ?? null,
      suggestion: link.suggestion ?? null,
    });
  }
  return entries;
}

/**
 * S1b (`PLAN-oi-replan-received-links.md`, AC-RL-20 to AC-RL-24): the muted amber
 * `reallocate`/`unlink` word beside the PO/SPO chip - one short word, the same "via PO"
 * pill idiom turned clickable, never an icon (17 Sep ruling: words, never icons).
 * Purchasing acts in AutoCount; nothing is written from here.
 */
function LinkSuggestionMark({
  suggestion,
  testId,
  onOpen,
}: {
  suggestion: OrderInquiryLinkSuggestion;
  testId: string;
  onOpen: (event: React.MouseEvent) => void;
}) {
  const word = suggestion.kind === 'reallocate' ? 'reallocate' : 'unlink';
  return (
    <WorklistPill as="button" warning testId={testId} onClick={onOpen}>
      {word}
    </WorklistPill>
  );
}

/**
 * `Reallocate to OI-000539 · SO420100 · needed 01/12/2026 · open 90`, or the rest of the
 * list plain (AC-RL-24, ruling 17 Sep: list every candidate, earliest first, the first
 * marked). Never "Repoint to" anywhere.
 */
function reallocateCandidateLine(
  candidate: {
    inquiry_no: string | null;
    item_code: string | null;
    so_number: string | null;
    delivery_date: string;
    open_qty: string;
  },
  isTarget: boolean,
): string {
  const prefix = isTarget ? 'Reallocate to ' : '';
  return (
    `${prefix}${candidate.inquiry_no ?? 'another inquiry'} · ` +
    `${candidate.so_number ?? 'unknown SO'} · ` +
    `needed ${formatDateInMalaysia(candidate.delivery_date)} · ` +
    `open ${formatInquiryQty(candidate.open_qty)}`
  );
}

/**
 * The lightbox `reallocate`/`unlink` opens (AC-RL-24): headed by the document, item and
 * quantity, then every candidate earliest first with the footer instruction - or, with
 * no candidates, `Unlink · no sooner inquiry needs this item`. No reason text, no
 * "early", no "repoint" anywhere - purchasing re-keys the line in AutoCount, and S5 (our
 * link follows the book) reacts to that; nothing is written from here.
 */
function LinkSuggestionDialog({
  row,
  document,
  qty,
  expectedDate,
  suggestion,
  open,
  onOpenChange,
}: {
  row: OrderInquiryWorklistRow;
  document: string;
  qty: string | null;
  expectedDate: string | null;
  suggestion: OrderInquiryLinkSuggestion;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg" data-testid={`link-suggestion-${row.id}`}>
        <DialogHeader>
          <DialogTitle>{document}</DialogTitle>
          <DialogDescription>
            {row.item_code ?? 'this item'} · {formatInquiryQty(qty ?? '0')}
            {expectedDate ? ` · expected ${formatDateInMalaysia(expectedDate)}` : ''}
            {row.delivery_date ? ` · needed ${formatDateInMalaysia(row.delivery_date)}` : ''}
          </DialogDescription>
        </DialogHeader>
        <DialogBody className="space-y-3">
          {suggestion.kind === 'unlink' ? (
            <p className="text-sm">Unlink · no sooner inquiry needs this item</p>
          ) : (
            <>
              <ul className="space-y-1.5 text-sm">
                {suggestion.candidates.map((candidate, index) => (
                  <li key={`${candidate.inquiry_no ?? 'candidate'}-${index}`}>
                    {reallocateCandidateLine(candidate, index === 0)}
                  </li>
                ))}
              </ul>
              <p className="text-xs text-muted-foreground">
                Re-key the line to the chosen sales order in AutoCount; the link moves at
                the next upload
              </p>
            </>
          )}
        </DialogBody>
      </DialogContent>
    </Dialog>
  );
}

/**
 * The PO cell and the SPO cell, which are the same cell over two books (owner, 14 Sep,
 * live look at prod: "1 column to show the linked PO and 1 column to show the linked SPO
 * (if linked to more than 1 then put as +1 pill) ... then I can click on the PO and SPO to
 * view the lightbox popup which is what we currently have").
 *
 * ONE LINE: the draft/confirmed mark, the first document number as the trigger, and a `+N`
 * pill when the row stands on more than one number of that book. The coverage headline and
 * the info icon left the cell in this slice - both already live in the lightbox, the
 * headline as its subtitle, and the number is a better trigger than an icon because it
 * answers the question ("which PO?") before it is clicked.
 *
 * A row linked in the OTHER book only reads as a muted dash here: it is linked, and this
 * is not where it is linked. Only a row linked in NEITHER book is a new order, and the PO
 * cell says that in words.
 *
 * The dialog mounts only once opened, so a page of a hundred rows does not carry two
 * hundred dialogs - the same pattern the info icon used.
 */
function DocumentsCell({ row, kind }: { row: OrderInquiryWorklistRow; kind: 'po' | 'spo' }) {
  const [open, setOpen] = React.useState(false);
  const [suggestionOpen, setSuggestionOpen] = React.useState(false);
  const numbers = documentsOf(row, kind);
  if (numbers.length === 0) return <Muted>-</Muted>;
  const [first, ...rest] = numbers;
  const what = row.item_code ?? row.so_number ?? 'this row';
  // The PO trigger keeps the id the info icon carried, so AC-A5's lightbox contract holds
  // and nothing that already points at it has to be told about this change.
  const triggerId =
    kind === 'spo' ? `backing-documents-trigger-spo-${row.id}` : `backing-documents-trigger-${row.id}`;
  const open_ = (event: React.MouseEvent) => {
    event.stopPropagation();
    setOpen(true);
  };
  return (
    <span className="flex min-w-0 items-center gap-1">
      <DraftMark row={row} />
      <button
        type="button"
        data-testid={triggerId}
        title={first.document}
        aria-label={`Show documents backing ${what}`}
        className="block min-w-0 truncate rounded-sm text-xs font-medium tabular-nums text-primary hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        onClick={open_}
      >
        {first.document}
      </button>
      {/* S5, R-E: never a real link - the SAME allocation read the other book's own
          number off (an SPO's `source_po_number`, or the PO's own open shipment). */}
      {first.via ? (
        <WorklistPill
          testId={
            kind === 'spo'
              ? `backing-documents-via-spo-${row.id}`
              : `backing-documents-via-${row.id}`
          }
        >
          {first.via === 'po' ? 'via PO' : 'via SPO'}
        </WorklistPill>
      ) : null}
      {/* S1, AC-RL-02 (17 Sep rulings): the document is fully received - location
          stock now, not a promise still in transit. ONE muted pill, same style as
          the "via" mark beside it, and CLICKABLE like every other mark on this row -
          opens the SAME lightbox, whose own body states the receipt in full. */}
      {first.received ? (
        <WorklistPill
          as="button"
          testId={
            kind === 'spo'
              ? `backing-documents-received-spo-${row.id}`
              : `backing-documents-received-${row.id}`
          }
          onClick={open_}
        >
          received
        </WorklistPill>
      ) : null}
      {/* S1b, AC-RL-24: a reallocate/unlink instruction, mutually exclusive with the
          `received` mark above (a received link never carries a suggestion). */}
      {first.suggestion ? (
        <LinkSuggestionMark
          suggestion={first.suggestion}
          testId={
            kind === 'spo'
              ? `backing-documents-suggestion-spo-${row.id}`
              : `backing-documents-suggestion-${row.id}`
          }
          onOpen={(event) => {
            event.stopPropagation();
            setSuggestionOpen(true);
          }}
        />
      ) : null}
      {rest.length ? (
        <Badge asChild size="sm" variant="secondary" appearance="light">
          <button
            type="button"
            data-testid={
              kind === 'spo'
                ? `backing-documents-pill-spo-${row.id}`
                : `backing-documents-pill-${row.id}`
            }
            aria-label={`Show all ${numbers.length} documents backing ${what}`}
            className="shrink-0 tabular-nums"
            onClick={open_}
          >
            +{rest.length}
          </button>
        </Badge>
      ) : null}
      {open ? (
        <OrderInquiryBackingDocumentsDialog row={row} open onOpenChange={setOpen} />
      ) : null}
      {first.suggestion && suggestionOpen ? (
        <LinkSuggestionDialog
          row={row}
          document={first.document}
          qty={first.qty}
          expectedDate={first.expectedDate}
          suggestion={first.suggestion}
          open
          onOpenChange={setSuggestionOpen}
        />
      ) : null}
    </span>
  );
}

/**
 * The Suggested column (`PLAN-oi-links-autocount-truth-24sep.md` section 3.5, AC-LT-01
 * to 03; R11/R12, owner rulings 24 Sep 2026, superseding the section's original mock):
 * a document the cascade SUGGESTS for this row - never a real link, never read by the
 * PO/SPO cells or the State pill above.
 *
 * The cell carries the document number ONLY - one per row, a plain `+N` when the row
 * holds more than one (R11: "don't need to show this, just make sure when i open the
 * SPO document i can see the line being highlighted"). No kind badge, no location, no
 * qty, no late marker, and no amber "suggested" word or pill (R12: "the suggested
 * column is good enough"). Opening the document from this cell opens the SAME
 * `OrderInquiryDocumentDialog` the PO/SPO cells use, and highlights the suggested line
 * in the lines grid (`suggestedLineId`) with the same idiom the linked line already
 * uses (issue #1215 point 2) - both able to show at once. `-` when the row carries none
 * (AC-LT-03).
 */
function SuggestedCell({ row }: { row: OrderInquiryWorklistRow }) {
  const suggestions = row.suggested_links ?? [];
  if (suggestions.length === 0) return <Muted>-</Muted>;
  const [first, ...rest] = suggestions;
  return (
    <span className="flex min-w-0 items-center gap-1">
      <OrderInquiryDocumentLink
        kind={first.kind}
        document={first.document}
        poId={first.po_id}
        suggestedLineId={first.kind === 'po' ? first.po_line_id : first.spo_allocation_id}
      />
      {rest.length ? (
        <Badge asChild size="sm" variant="secondary" appearance="light">
          <span
            data-testid={`suggested-pill-${row.id}`}
            className="shrink-0 tabular-nums"
            // Nit 1 (review round 2): the cell shows one document (R11); the rest
            // are a hover away rather than hidden entirely.
            title={rest.map((suggestion) => suggestion.document).join(', ')}
          >
            +{rest.length}
          </span>
        </Badge>
      ) : null}
    </span>
  );
}

/** The Suggested column def, shared between the worklist and the OI detail Lines tab
 * (AC-LT-07) - same reasons `orderInquirySoLineColumn` is factored out below. */
export function orderInquirySuggestedColumn(): ColumnDef<OrderInquiryWorklistRow> {
  return {
    id: 'suggested',
    accessorFn: (row) => row.suggested_links?.[0]?.document ?? '',
    header: ({ column }) => <DataGridColumnHeader title="Suggested" column={column} />,
    size: 220,
    meta: { headerTitle: 'Suggested', skeleton: <Skeleton className="h-4 w-24" /> },
    cell: ({ row }) => <SuggestedCell row={row.original} />,
  };
}

/**
 * The PO cell's info icon for a BUNDLED row (UAC D1-D3, D10;
 * PLAN-scm-supplied-with-companions.md section 3.4). The only info icon left on this list
 * since S3 (14 Sep): a bundled row has no document number of its own to click, so the icon
 * is still the way into the anchor's lightbox.
 *
 * A row that rides ENTIRELY inside the item(s) it is bundled with has no backing
 * documents of its own - the icon opens the ANCHOR row's own lightbox instead ("the
 * info icon opens the HOST row's lightbox", never named that in the UI). A row that is
 * only PART bundled keeps its own lightbox (its own links back the ala carte
 * remainder), with a leading entry naming what the rest rides with.
 */
function BundledDocumentsButton({
  row,
  anchorRow,
  fullyBundled,
}: {
  row: OrderInquiryWorklistRow;
  anchorRow: OrderInquiryWorklistRow | null;
  fullyBundled: boolean;
}) {
  const [open, setOpen] = React.useState(false);
  const bundled = row.bundled_with;
  if (!bundled) return null;
  const itemCodes = bundled.item_codes.length ? bundled.item_codes : [bundled.item_code];
  const dialogRow = fullyBundled ? (anchorRow ?? row) : row;
  return (
    <>
      <Button
        type="button"
        mode="icon"
        variant="ghost"
        size="sm"
        data-testid={`backing-documents-trigger-${row.id}`}
        aria-label={`Show documents backing ${row.item_code ?? row.so_number ?? 'this row'}`}
        className="size-5 shrink-0 text-muted-foreground"
        onClick={(event) => {
          event.stopPropagation();
          setOpen(true);
        }}
      >
        <Info className="size-3.5" aria-hidden />
      </Button>
      {open ? (
        <OrderInquiryBackingDocumentsDialog
          row={dialogRow}
          open
          onOpenChange={setOpen}
          bundleNote={
            fullyBundled
              ? { itemCodes, fullyBundled: true }
              : { itemCodes, fullyBundled: false, qty: row.bundled_qty ?? '0' }
          }
        />
      ) : null}
    </>
  );
}

/**
 * The Qty cell's own trigger (owner's 9 Sep feedback, live look at the running lane): the
 * same one-line defect slice A fixed for the Outstanding column also sat here - a rejected
 * row's reason or a changed row's Was/Now table rendered as a second line, so those rows
 * read taller than every other one. Rendered ONLY when the row actually has something to
 * say; a plain acknowledged row shows the quantity alone.
 *
 * A rejected or changed row keeps the info icon (the design system's own warning token for
 * a rejection, matching `Badge variant="warning"` elsewhere on this screen; muted for a
 * plain change) - both facts win the warning colour when both apply, a rejection being the
 * more urgent of the two.
 *
 * A row a replan REDIRECTED (AC-RL-04) or one AutoCount's own book pairing MOVED off
 * entirely (AC-RL-46) reads as a one-word muted pill instead - `used` or `note` - never an
 * icon (17 Sep ruling: words, never icons, for every mark this list carries). Both open the
 * SAME dialog this icon does; only the trigger's own shape differs.
 *
 * A BUNDLED row (`PLAN-oi-bundled-row-host-change.md`) carries none of the above - a
 * companion has no sheet row, no PO and no Was of its own - so its icon opens a TOOLTIP
 * instead of the dialog, one line per host, read straight off `bundled_host_changes`
 * (owner ruling: "it comes with the X and Y, so it should follow them, to have the same
 * delay"). Only when nothing else already claims the icon: a row that is ALSO rejected or
 * changed shows that dialog first, exactly as today.
 */
function QtyAnnotationButton({ row }: { row: OrderInquiryWorklistRow }) {
  const [open, setOpen] = React.useState(false);
  const rejected = ackStateOf(row) === 'rejected';
  const changed = Boolean(previousValueOf(row));
  // AC-RL-04 (17 Sep rulings): the row's only coverage had already landed elsewhere by
  // the time a replan met it - kept here as history, marked `used` rather than the
  // retired `redirected` pill.
  const redirected = Boolean(row.redirected_to_pool);
  // AC-RL-46 (`PLAN-oi-replan-received-links.md` S5): a row the book redirected off is
  // neither rejected, changed nor redirected-to-pool - a settle never touched it - so the
  // gate widens to the same note `follow_book_repairing` wrote, the only other fact this
  // trigger ever shows. Never checked on a `redirected_to_pool` row: that row's own note
  // reads differently and is handled by the branch above.
  const moved = !redirected ? movedNoteOf(row) : null;
  const hostLines =
    !rejected && !changed && !redirected && !moved ? bundledHostChangeLines(row) : null;
  if (!rejected && !changed && !redirected && !moved && !hostLines) return null;

  if (hostLines) {
    return (
      <Tooltip>
        <TooltipTrigger asChild>
          <Button
            type="button"
            mode="icon"
            variant="ghost"
            size="sm"
            data-testid={`qty-annotation-trigger-${row.id}`}
            aria-label={`Show what ${row.item_code ?? row.so_number ?? 'this row'} rides with`}
            className="size-5 shrink-0 text-muted-foreground"
          >
            <Info className="size-3.5" aria-hidden />
          </Button>
        </TooltipTrigger>
        <TooltipContent className="max-w-xs break-words">
          {hostLines.map((line) => (
            <div key={line}>{line}</div>
          ))}
        </TooltipContent>
      </Tooltip>
    );
  }

  const openDialog = (event: React.MouseEvent) => {
    event.stopPropagation();
    setOpen(true);
  };

  if (redirected || moved) {
    const word = redirected ? 'used' : 'note';
    const label = redirected
      ? `Show why ${row.item_code ?? row.so_number ?? 'this row'} was redirected`
      : `Show what AutoCount changed on ${row.item_code ?? row.so_number ?? 'this row'}`;
    return (
      <>
        <WorklistPill
          as="button"
          testId={`qty-annotation-trigger-${row.id}`}
          ariaLabel={label}
          onClick={openDialog}
        >
          {word}
        </WorklistPill>
        {open ? (
          <OrderInquiryQtyAnnotationDialog row={row} open onOpenChange={setOpen} />
        ) : null}
      </>
    );
  }

  const label = rejected
    ? `Show why ${row.item_code ?? row.so_number ?? 'this row'} was rejected`
    : `Show what changed on ${row.item_code ?? row.so_number ?? 'this row'}`;
  return (
    <>
      <Button
        type="button"
        mode="icon"
        variant="ghost"
        size="sm"
        data-testid={`qty-annotation-trigger-${row.id}`}
        aria-label={label}
        className={`size-5 shrink-0 ${
          rejected
            ? 'text-[var(--color-warning-accent,var(--color-yellow-700))]'
            : 'text-muted-foreground'
        }`}
        onClick={openDialog}
      >
        <Info className="size-3.5" aria-hidden />
      </Button>
      {open ? (
        <OrderInquiryQtyAnnotationDialog row={row} open onOpenChange={setOpen} />
      ) : null}
    </>
  );
}

/**
 * The cell renderers below (`ItemCodeCell` .. `InstructionCell`) are exported so the OI
 * DETAIL page's own Lines tab (`PLAN-oi-header-list-detail.md`, S5) reuses exactly these
 * renderings rather than a second copy that could drift from the worklist's own words -
 * the column defs in `useOrderInquiryWorklistColumns` below call the very same functions.
 * PO/SPO are deliberately NOT among them: the detail page's own cells open the simpler
 * `OrderInquiryDocumentDialog` (AC-DP-04), not this worklist's bundling/reallocate-aware
 * `DocumentsCell`, which reasons about columns this single-header screen has no use for.
 */
export function ItemCodeCell({
  row,
  codeOnly = false,
}: {
  row: OrderInquiryWorklistRow;
  /**
   * AC-DP-03, owner ruling 21 Sep: the OI detail page's own Lines tab shows the
   * product CODE only, one line - "in Sorento the product code IS the product
   * name" - even though a real worklist row DOES carry `product_name`. Defaults to
   * `false` so this cell's every OTHER caller (the Lines worklist itself) renders
   * exactly as it always has; only `orderInquiryHeaderLinesColumns.tsx` passes
   * `true`.
   */
  codeOnly?: boolean;
}) {
  return (
    <div className="min-w-0">
      <span className="block truncate font-medium" title={row.item_code ?? ''}>
        {row.item_code || <Muted>Unresolved</Muted>}
      </span>
      {row.previous_item_code && row.previous_item_code !== row.item_code && (
        // OI-PRODUCT-FOLLOW S3: the product a Confirm moved the row off, kept visible
        // the way a qty/date change keeps its old value.
        <span
          className="block truncate text-xs text-muted-foreground"
          title={`was ${row.previous_item_code}`}
        >
          was {row.previous_item_code}
        </span>
      )}
      {!codeOnly && row.product_name && row.product_name !== row.item_code && (
        <span className="block truncate text-xs text-muted-foreground" title={row.product_name}>
          {row.product_name}
        </span>
      )}
    </div>
  );
}

export function QtyCell({ row }: { row: OrderInquiryWorklistRow }) {
  return (
    <span className="flex min-w-0 items-center gap-1 tabular-nums">
      {formatInquiryQty(row.qty)}
      <QtyAnnotationButton row={row} />
      {row.line_cancelled ? (
        <WorklistPill testId={`qty-line-cancelled-${row.id}`}>cancelled</WorklistPill>
      ) : null}
    </span>
  );
}

/**
 * AC-B2-0 (`PLAN-board-oi-mechanical-22sep.md`, S2 round): a settle-in-place restates the
 * SAME buy row with a new date and drops its ack back to `changed` - purchasing needs to
 * see that here, beside the date, rather than a dedicated Ack column (there is none). The
 * (i) reading "Was <qty> on <old date>" is the Qty cell's own `QtyAnnotationButton`; this
 * tag is the OTHER half of the same fact, read off `ack_state` rather than `previous_qty`
 * so it can never disagree about which rows still need a look.
 */
export function DeliveryDateCell({ row }: { row: OrderInquiryWorklistRow }) {
  const changed = ackStateOf(row) === 'changed';
  if (!row.delivery_date) return <Muted>No date</Muted>;
  return (
    <span className="flex min-w-0 items-center gap-1 whitespace-nowrap">
      {formatDateInMalaysia(row.delivery_date)}
      {changed ? <WorklistPill testId={`delivery-date-changed-${row.id}`}>Changed</WorklistPill> : null}
    </span>
  );
}

/**
 * S6 (`PLAN-board-oi-mechanical-22sep.md`, AC-B6-1): `SO402757 · L5`, linking to that
 * exact sales-order line. The OI detail Lines tab's own "SO line" column (below) - it has
 * no S/O no column of its own to carry this on. Fix round (22 Sep): the worklist's OWN
 * "SO line" column was DROPPED - it sat beside the pre-existing S/O no column and printed
 * the same SO number twice per row; the worklist's S/O no cell carries this same label and
 * href directly instead (`useOrderInquiryWorklistColumns`'s `so_number` column).
 */
export function SoLineCell({ row }: { row: OrderInquiryWorklistRow }) {
  const label = orderInquirySoLineLabel(row);
  const href = orderInquirySoLineHref(row);
  if (!href) {
    return (
      <span className="block truncate" title={label}>
        {label}
      </span>
    );
  }
  return (
    <Link href={href} className="block truncate font-medium text-primary hover:underline" title={label}>
      {label}
    </Link>
  );
}

export function TakenCell({ row }: { row: OrderInquiryWorklistRow }) {
  return <span className="tabular-nums">{inquiryRowTaken(row)}</span>;
}

export function RemainingCell({ row }: { row: OrderInquiryWorklistRow }) {
  return <span className="tabular-nums">{inquiryRowRemaining(row)}</span>;
}

/**
 * Taken / Remaining (S3, `PLAN-board-oi-mechanical-22sep.md`, AC-B3-1..7), shared between
 * the OI detail Lines tab and this worklist (AC-B3-1, AC-B3-6) so the two screens can never
 * state what a row has taken two different ways. `pageScoped` only changes the FOOTER's own
 * words: the worklist paginates server-side, so its footer totals the page and says so
 * (AC-B3-6); the Lines tab loads every line at once, so its footer is simply the total.
 */
function FooterTotal({
  table,
  field,
  pageScoped,
}: {
  table: Table<OrderInquiryWorklistRow>;
  field: 'qty' | 'taken' | 'remaining';
  pageScoped: boolean;
}) {
  const totals = inquiryFooterTotals(table.getPrePaginationRowModel().rows.map((r) => r.original));
  return (
    <span className="tabular-nums">
      {formatInquiryQty(String(totals[field]))}
      {pageScoped ? <span className="text-muted-foreground"> (page)</span> : null}
    </span>
  );
}

/**
 * S6 (AC-B6-1): the "SO line" column. Fix round (22 Sep): only the OI detail Lines tab
 * (`orderInquiryHeaderLinesColumns.tsx`) uses this now - it has no S/O no column of its
 * own to carry the link on. The worklist's S/O no cell carries the same label/href
 * directly instead of a second column (see `SoLineCell`'s own doc comment above).
 */
export function orderInquirySoLineColumn(): ColumnDef<OrderInquiryWorklistRow> {
  return {
    id: 'so_line',
    accessorFn: (row) => orderInquirySoLineLabel(row),
    header: ({ column }) => <DataGridColumnHeader title="SO line" column={column} />,
    size: 160,
    meta: { headerTitle: 'SO line', skeleton: <Skeleton className="h-4 w-20" /> },
    cell: ({ row }) => <SoLineCell row={row.original} />,
  };
}

export function orderInquiryTakenRemainingColumns({
  pageScoped = false,
}: { pageScoped?: boolean } = {}): ColumnDef<OrderInquiryWorklistRow>[] {
  return [
    {
      id: 'taken',
      header: ({ column }) => <DataGridColumnHeader title="Taken" column={column} />,
      size: 110,
      enableSorting: false,
      meta: { headerTitle: 'Taken', skeleton: <Skeleton className="h-4 w-10" /> },
      cell: ({ row }) => <TakenCell row={row.original} />,
      footer: ({ table }) => <FooterTotal table={table} field="taken" pageScoped={pageScoped} />,
    },
    {
      id: 'remaining',
      header: ({ column }) => <DataGridColumnHeader title="Remaining" column={column} />,
      size: 120,
      enableSorting: false,
      meta: { headerTitle: 'Remaining', skeleton: <Skeleton className="h-4 w-10" /> },
      cell: ({ row }) => <RemainingCell row={row.original} />,
      footer: ({ table }) => <FooterTotal table={table} field="remaining" pageScoped={pageScoped} />,
    },
  ];
}

export function SupplierCell({ row }: { row: OrderInquiryWorklistRow }) {
  return row.supplier ? (
    <span className="block truncate" title={row.supplier}>
      {row.supplier}
    </span>
  ) : (
    <Muted>Not linked</Muted>
  );
}

export function LocationCell({ row }: { row: OrderInquiryWorklistRow }) {
  return row.location ? (
    <span className="block truncate" title={row.location}>
      {row.location}
    </span>
  ) : null;
}

export function InstructionCell({ row }: { row: OrderInquiryWorklistRow }) {
  return (
    <div className="flex min-w-0 items-center gap-1.5">
      <OrderInquiryVerbPill verb={row.verb} />
      {row.note && (
        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              type="button"
              mode="icon"
              variant="ghost"
              size="sm"
              aria-label="Why this instruction"
              className="size-5 shrink-0 text-muted-foreground"
            >
              <Info className="size-3.5" aria-hidden />
            </Button>
          </TooltipTrigger>
          <TooltipContent className="max-w-xs break-words">{row.note}</TooltipContent>
        </Tooltip>
      )}
    </div>
  );
}

/**
 * AC-DT-6 (`PLAN-oi-decision-trail-ui.md`): `<Kind> by <name> · <date time>`, or the
 * bare kind word when the server matched no event (`Sheet` / `Planning change`, which
 * carry no `raise_event_by_name`/`raise_event_at`), or a dash when nothing at all is
 * known about how the row was raised. Shared with the Lines tab
 * (`orderInquiryHeaderLinesColumns.tsx`) so the same row reads the same way there.
 */
export function RaisedCell({ row }: { row: OrderInquiryWorklistRow }) {
  const kind = raisedKindLabel(row);
  if (!kind) return <Muted>-</Muted>;
  // A sheet row reads the bare word: whatever event the window matched, nobody in this
  // system raised it, and a name or a time beside "Sheet" would say somebody did.
  const text =
    kind === 'Sheet'
      ? kind
      : `${kind}${row.raise_event_by_name ? ` by ${row.raise_event_by_name}` : ''}${
          row.raise_event_at ? ` · ${formatDateTimeInMalaysia(row.raise_event_at)}` : ''
        }`;
  return (
    <span className="block truncate" title={text}>
      {text}
    </span>
  );
}

/**
 * The worklist's columns, in the spreadsheet's own order (`JAN - DEC 2026 ORDER.xlsx`).
 *
 * Shared between the main list and the calendar's day drilldown, so a person reading
 * either sees the same columns in the same order rather than a second, looser table
 * invented for the calendar.
 */
export function useOrderInquiryWorklistColumns({
  selectable = false,
}: {
  /**
   * Draw the row checkboxes (AC-H2). Only where a bulk bar can act on them: the calendar
   * drilldown reuses these columns and has no Confirm press, so a tick there would
   * select rows nothing could be done with.
   */
  selectable?: boolean;
} = {}): ColumnDef<OrderInquiryWorklistRow>[] {
  return React.useMemo<ColumnDef<OrderInquiryWorklistRow>[]>(() => {
    const columns: ColumnDef<OrderInquiryWorklistRow>[] = [
      ...(selectable
        ? [
            buildSelectColumn<OrderInquiryWorklistRow>({
              // Informational only - TanStack reads `getCanSelect` off the TABLE's own
              // `enableRowSelection`, never a column's (`OrderInquiriesClient`'s own
              // note over the same trap), which is where R-A's rule actually lives now
              // (S4, PLAN-scm-oi-worklist-excel-parity.md): every row except `cancelled`
              // ticks, fully linked rows included. Kept here only so this column's own
              // `disabledReason` still applies to the one row the table itself blocks.
              disabledReason: (row) =>
                row.original.state === 'cancelled'
                  ? 'This instruction was called off'
                  : undefined,
              rowLabel: (row) =>
                `Select ${row.original.item_code ?? 'row'} on ${row.original.so_number ?? 'this order'}`,
            }),
          ]
        : []),
      {
        accessorKey: 'so_date',
        header: ({ column }) => <DataGridColumnHeader title="SO date" column={column} />,
        size: 120,
        meta: { headerTitle: 'SO date', skeleton: <Skeleton className="h-4 w-20" /> },
        cell: ({ row }) =>
          row.original.so_date ? (
            <span className="whitespace-nowrap">
              {formatDateInMalaysia(row.original.so_date)}
            </span>
          ) : (
            <Muted>No date</Muted>
          ),
      },
      {
        accessorKey: 'so_number',
        // "S/O line", not "S/O no" (review round, 22 Sep): the cell prints `SO402757 · L5`
        // now, so the old heading named half of what is under it. The column id is
        // untouched, so a saved layout keeps its place.
        header: ({ column }) => <DataGridColumnHeader title="S/O line" column={column} />,
        size: 150,
        meta: { headerTitle: 'S/O line', skeleton: <Skeleton className="h-4 w-20" /> },
        // The way in - AND (fix round, S6) the deep link (AC-B6-1): `SO402757 · L5` once
        // the row carries a line number, the bare SO number otherwise, linking straight to
        // the exact sales-order LINE when both `core_sales_order_id` and `core_line_id`
        // are on the row. A second "SO line" column here duplicated this cell's own text
        // (every row prints the same SO number twice) - the worklist has ONE S/O column,
        // and it carries the line; the OI detail Lines tab, which has no S/O no column of
        // its own, keeps the separate `orderInquirySoLineColumn()` below. Falls back to
        // `orderInquiryRowHref` (the project document route) when no core line resolves -
        // an adopted row reaches the CORE sales order and an authored one its project
        // document; a row that can reach neither is plain text rather than a link that
        // answers 404.
        cell: ({ row }) => {
          const label = orderInquirySoLineLabel(row.original);
          const href = orderInquirySoLineHref(row.original) ?? orderInquiryRowHref(row.original);
          if (!href)
            return (
              <span className="block truncate" title={label}>
                {label}
              </span>
            );
          return (
            <Link
              href={href}
              className="block truncate font-medium text-primary hover:underline"
              title={label}
            >
              {label}
            </Link>
          );
        },
      },
      {
        accessorKey: 'item_code',
        header: ({ column }) => <DataGridColumnHeader title="Item code" column={column} />,
        size: 180,
        meta: { headerTitle: 'Item code', skeleton: <Skeleton className="h-4 w-24" /> },
        cell: ({ row }) => <ItemCodeCell row={row.original} />,
      },
      {
        // Qty carries the handshake now (S1, AC-1.5): a rejection and its reason, or a
        // settle-in-place and its Was/Now, are the two facts that still matter to a buyer
        // scanning the row. ONE LINE (AC-A8, owner's 9 Sep feedback against the running
        // lane - the same defect slice A fixed for the Outstanding column): the quantity,
        // and an info icon only when there is something to say. A row that is simply
        // acknowledged (the ordinary case) shows the number and nothing else.
        accessorKey: 'qty',
        header: ({ column }) => <DataGridColumnHeader title="Qty" column={column} />,
        // Review fix round (20 Sep 2026): the cell truncates and every child is
        // `shrink-0` - a row that is both `used` and on a cancelled line needs room
        // for the number, the used pill, the Was/Now icon, the cancelled pill and
        // the gaps/padding between them (~174px measured).
        size: 200,
        meta: { headerTitle: 'Qty', skeleton: <Skeleton className="h-4 w-10" /> },
        cell: ({ row }) => <QtyCell row={row.original} />,
      },
      {
        accessorKey: 'delivery_date',
        header: ({ column }) => (
          <DataGridColumnHeader title="Delivery date" column={column} />
        ),
        size: 140,
        meta: { headerTitle: 'Delivery date', skeleton: <Skeleton className="h-4 w-20" /> },
        cell: ({ row }) => <DeliveryDateCell row={row.original} />,
      },
      {
        // PLAN-oi-worklist-split-customer-project.md (owner, 18 Sep: "here need to
        // split the customer and project out"): Customer and Project, two columns
        // where the combined `project_customer` used to print. `project_customer`
        // itself stays on the row for the Excel export and the search box, unchanged.
        accessorKey: 'customer_name',
        header: ({ column }) => <DataGridColumnHeader title="Customer" column={column} />,
        size: 150,
        meta: { headerTitle: 'Customer', skeleton: <Skeleton className="h-4 w-24" /> },
        cell: ({ row }) =>
          row.original.customer_name ? (
            <span className="block truncate" title={row.original.customer_name}>
              {row.original.customer_name}
            </span>
          ) : (
            <Muted>Not attributed</Muted>
          ),
      },
      {
        accessorKey: 'project_title',
        header: ({ column }) => <DataGridColumnHeader title="Project" column={column} />,
        size: 180,
        meta: { headerTitle: 'Project', skeleton: <Skeleton className="h-4 w-32" /> },
        cell: ({ row }) =>
          row.original.project_title ? (
            <span className="block truncate" title={row.original.project_title}>
              {row.original.project_title}
            </span>
          ) : (
            <Muted>No project</Muted>
          ),
      },
      {
        accessorKey: 'supplier',
        header: ({ column }) => <DataGridColumnHeader title="Supplier" column={column} />,
        size: 150,
        meta: { headerTitle: 'Supplier', skeleton: <Skeleton className="h-4 w-20" /> },
        // Blank means nobody has linked it yet, exactly as a blank cell does on their
        // sheet. Never filled in with a guess at who would supply it.
        cell: ({ row }) => <SupplierCell row={row.original} />,
      },
      {
        // WHICH PURCHASE ORDER this row stands on (AC-R-26..R-31, owner 14 Sep, live look
        // at prod after the migration upload). The cell is ONE LINE: the draft/confirmed
        // mark, the first PO number as the trigger for the backing-documents lightbox, and
        // a `+N` pill when the row stands on more than one. The coverage headline and the
        // info icon left this cell in that slice - both are in the lightbox already (the
        // headline is its subtitle), and the owner's question of the list is "which PO",
        // which an icon cannot answer until it is clicked.
        //
        // The column id stays `po_number`: it is what a saved column layout is keyed by,
        // and renaming it would silently exile the column to the right of everyone's grid.
        id: 'po_number',
        accessorFn: (row) => documentsOf(row, 'po')[0]?.document ?? '',
        header: ({ column }) => <DataGridColumnHeader title="PO" column={column} />,
        // Wide enough for `202605-S0005` AND the "via SPO" tag beside it at 1280 (review
        // round): at 150 the number itself truncated the moment a row's PO was derived,
        // which is the one row where reading the whole number matters.
        size: 200,
        meta: { headerTitle: 'PO', skeleton: <Skeleton className="h-4 w-24" /> },
        cell: ({ row, table }) => {
          const bundled = row.original.bundled_with;
          const bundledQty = Number(row.original.bundled_qty ?? '0');
          if (bundled && Number.isFinite(bundledQty) && bundledQty > 0) {
            const headline = bundledHeadline(row.original);
            if (headline) {
              // The FULL anchor row, for the info icon's own lightbox only (it needs
              // the anchor's real documents, not just its headline) - the headline text
              // above never depends on this: it comes straight off `bundled.anchor_headline`,
              // resolved server-side. `table.options.data` is the loaded rows, never a
              // second fetch; a page that does not happen to hold the anchor falls back
              // to the row's own lightbox (`dialogRow` inside `BundledDocumentsButton`).
              const rows = table.options.data as OrderInquiryWorklistRow[];
              const anchorRow = rows.find((r) => r.id === bundled.row_id) ?? null;
              const qty = Number(row.original.qty ?? '0');
              const fullyBundled = qty - bundledQty <= 0;
              return (
                <span className="flex min-w-0 items-center gap-1 text-xs font-medium tabular-nums">
                  <DraftMark row={row.original} />
                  <span className="truncate" title={headline}>
                    {headline}
                  </span>
                  <BundledDocumentsButton
                    row={row.original}
                    anchorRow={anchorRow}
                    fullyBundled={fullyBundled}
                  />
                </span>
              );
            }
          }
          if ((row.original.links ?? []).length === 0) {
            // Nothing in either book can cover this row (S5, AC-D4): a plain dash, the
            // same "no explanation in the UI" rule every other blank cell here follows -
            // "Not found (new order)" read as a caption nobody asked for. Nothing is
            // clickable: there is nothing behind it to open.
            return (
              <div className="min-w-0">
                <Muted>-</Muted>
              </div>
            );
          }
          // Linked in the other book only: a muted dash, and the SPO cell beside this one
          // is where that row says where it stands.
          return <DocumentsCell row={row.original} kind="po" />;
        },
      },
      {
        // WHICH SHIPPING ORDER this row stands on - the other half of the owner's ruling,
        // and a new column rather than a second line in the PO cell, because "I want all
        // rows to have 1 line only".
        id: 'spo_number',
        accessorFn: (row) => documentsOf(row, 'spo')[0]?.document ?? '',
        header: ({ column }) => <DataGridColumnHeader title="SPO" column={column} />,
        // Same width as PO beside it, for the same reason - plus "awaiting shipment",
        // which this column prints in full.
        size: 200,
        meta: { headerTitle: 'SPO', skeleton: <Skeleton className="h-4 w-24" /> },
        cell: ({ row }) => {
          // A bundled row's documents are the anchor's, and the PO cell already says so
          // in words (`Included with ...`) with the bundled lightbox behind it. Repeating
          // any of that here would make the bundle read as two separate facts.
          const bundledQty = Number(row.original.bundled_qty ?? '0');
          const bundled =
            row.original.bundled_with && Number.isFinite(bundledQty) && bundledQty > 0;
          if (bundled) return <Muted>-</Muted>;
          // S5, AC-D4: bought but not yet on a shipment - distinct from a plain dash,
          // which means nobody has put this row anywhere at all.
          if (
            documentsOf(row.original, 'spo').length === 0 &&
            documentsOf(row.original, 'po').length > 0
          ) {
            return <Muted>awaiting shipment</Muted>;
          }
          return <DocumentsCell row={row.original} kind="spo" />;
        },
      },
      // AC-LT-01 to 03/08: the Suggested column, right after SPO - never merged into the
      // PO/SPO cells above, which read real links only.
      orderInquirySuggestedColumn(),
      {
        accessorKey: 'agent_code',
        header: ({ column }) => <DataGridColumnHeader title="Agent" column={column} />,
        size: 110,
        meta: { headerTitle: 'Agent', skeleton: <Skeleton className="h-4 w-14" /> },
        // Who sold it, off the core sales order. Blank when the row reaches no core order
        // or that order carries no agent - never a guess.
        cell: ({ row }) =>
          row.original.agent_code ? (
            <span
              className="block truncate"
              title={row.original.agent_label || row.original.agent_code}
            >
              {row.original.agent_code}
            </span>
          ) : (
            <Muted>Not assigned</Muted>
          ),
      },
      {
        accessorKey: 'location',
        header: ({ column }) => <DataGridColumnHeader title="Location" column={column} />,
        size: 130,
        meta: { headerTitle: 'Location', skeleton: <Skeleton className="h-4 w-16" /> },
        // Where the PO gets placed for, not where the item is bought TO. Blank when
        // nobody has stamped a location and the line has no fulfilment warehouse either -
        // never a dash standing in for "unknown".
        cell: ({ row }) => <LocationCell row={row.original} />,
      },
      {
        accessorKey: 'inquiry_no',
        header: ({ column }) => (
          <DataGridColumnHeader title="Order inquiry" column={column} />
        ),
        size: 130,
        meta: { headerTitle: 'Order inquiry', skeleton: <Skeleton className="h-4 w-20" /> },
        // Which instruction this row belongs to, by the number purchasing quotes. An
        // amendment raises a SECOND inquiry on the same sales order, so the S/O no beside
        // it cannot answer "which one was I told about".
        cell: ({ row }) =>
          row.original.inquiry_no ? (
            <span className="block truncate tabular-nums" title={row.original.inquiry_no}>
              {row.original.inquiry_no}
            </span>
          ) : (
            <Muted>Not numbered</Muted>
          ),
      },
      // S3 (`PLAN-board-oi-mechanical-22sep.md`, AC-B3-6): the SHARED, row-level Taken /
      // Remaining pair, same factory the OI detail Lines tab uses - superseding the older
      // `taken_from_po` / `remaining_open` line-level pair, which read every sibling
      // ORDER/ORDER BACK row's links on the same SO line rather than this row's own.
      // `pageScoped`: this grid paginates server-side (`manualPagination`), so `rows` IS
      // already the current page and the footer says so.
      ...orderInquiryTakenRemainingColumns({ pageScoped: true }),
      {
        accessorKey: 'verb',
        header: ({ column }) => <DataGridColumnHeader title="Instruction" column={column} />,
        size: 210,
        meta: { headerTitle: 'Instruction', skeleton: <Skeleton className="h-4 w-24" /> },
        // The verb is what purchasing DOES with the row: an ORDER and an ORDER BACK both
        // cost money, a CANCEL BALANCE takes it back, and the state alone tells them apart
        // from nothing. The server's own sentence ("Borrowed N for SOxxx line n; CODE goes
        // short by q") is the reasoning behind the verb, not the instruction itself, so it
        // moves behind the info icon rather than sitting inline under the pill.
        // Qty already has its own column; repeating it here duplicated the number rather
        // than adding to it.
        //
        // `PLAN-oi-request-cs-reserve.md` 3.5: the reserve chip sits beside it here,
        // OUTSIDE the shared `InstructionCell` (which the Lines tab also calls, in ITS
        // OWN "Instruction" column, separate from where its "State" column already
        // carries this same pill) - adding it to `InstructionCell` itself would show it
        // twice on that other screen.
        cell: ({ row }) => (
          <div className="flex min-w-0 flex-wrap items-center gap-1">
            <InstructionCell row={row.original} />
            <ReservePill
              reserveState={row.original.reserve_state}
              reservedQty={row.original.reserved_qty}
            />
            {/* AC-DT-5 (`PLAN-oi-decision-trail-ui.md`, round 2): the decision trail
                icon, beside the row's own state-ish marks - there is no separate "State"
                column on this worklist (the Lines tab has one; see the note there), so
                this is where a state-like pill already sits. On EVERY row, not only a
                reserved one: `DecisionTrailButton` itself hides when the row names no
                core sales-order line at all. */}
            <DecisionTrailButton
              coreLineId={row.original.core_line_id ?? null}
              itemCode={row.original.item_code}
              className="size-5 shrink-0 text-muted-foreground"
            />
          </div>
        ),
      },
      {
        // AC-DT-6 (`PLAN-oi-decision-trail-ui.md`): the trail behind the instruction -
        // Raised, Reconfirmed, Sheet or Planning change, by whom, when. Hidden by
        // default here (`DEFAULT_HIDDEN_COLUMNS`); the OI detail Lines tab hides it by
        // default too now (round 2 ruling - column preferences still let it on either
        // screen). Unsortable: it is a derived, per-row match against
        // `order_inquiry_raises`, not a plain column. `accessorFn` is what the column
        // picker itself keys "can this be listed" on (`data-grid-column-visibility.tsx`),
        // so a bare `id` + `cell` (round 1's own shape) made this column impossible to
        // ever turn back on. "Raised via", not "Raised": the "Raised by" column sits
        // right beside this one (captain ruling, review round 1).
        id: 'raise_event',
        accessorFn: (row) => raisedKindLabel(row) ?? '',
        header: ({ column }) => <DataGridColumnHeader title="Raised via" column={column} />,
        size: 220,
        enableSorting: false,
        meta: { headerTitle: 'Raised via', skeleton: <Skeleton className="h-4 w-24" /> },
        cell: ({ row }) => <RaisedCell row={row.original} />,
      },
      {
        // WHO pushed this to purchasing. Sorted server-side on the person's name, which
        // is why the column id is `raised_by_name` rather than `raised_by`: the id is
        // what the filter sends, the name is what this column is about.
        accessorKey: 'raised_by_name',
        header: ({ column }) => <DataGridColumnHeader title="Raised by" column={column} />,
        size: 150,
        meta: { headerTitle: 'Raised by', skeleton: <Skeleton className="h-4 w-24" /> },
        cell: ({ row }) =>
          row.original.raised_by_name ? (
            <span className="block truncate" title={row.original.raised_by_name}>
              {row.original.raised_by_name}
            </span>
          ) : (
            <Muted>Not recorded</Muted>
          ),
      },
      {
        // The TIME, not just the day: two revisions of the same order are raised hours
        // apart, and a date alone cannot tell them apart. Malaysian wall clock, from a
        // naive UTC stamp.
        accessorKey: 'raised_at',
        header: ({ column }) => <DataGridColumnHeader title="Raised at" column={column} />,
        // 190, not 170 (N6, review round 1): the date/time plus the new info icon no
        // longer fit the old width without crowding the icon against the next column.
        size: 190,
        meta: { headerTitle: 'Raised at', skeleton: <Skeleton className="h-4 w-24" /> },
        // PLAN-oi-worklist-split-customer-project.md: a re-confirm cancels a carried line's row
        // and raises a fresh one, so this cell's own date moves on - the info icon is
        // where the earlier raise(s) still show, same Info + Tooltip pattern as the
        // Instruction column's "why this instruction" above.
        cell: ({ row }) => {
          const history = row.original.raise_history ?? [];
          return row.original.raised_at ? (
            <span className="flex min-w-0 items-center gap-1 whitespace-nowrap">
              {formatDateTimeInMalaysia(row.original.raised_at)}
              {history.length > 0 && (
                <Tooltip>
                  <TooltipTrigger asChild>
                    <Button
                      type="button"
                      mode="icon"
                      variant="ghost"
                      size="sm"
                      aria-label="Previously raised"
                      className="size-5 shrink-0 text-muted-foreground"
                    >
                      <Info className="size-3.5" aria-hidden />
                    </Button>
                  </TooltipTrigger>
                  <TooltipContent className="max-w-xs break-words">
                    <p className="font-medium">Previously raised</p>
                    {history.map((entry, index) => (
                      <p key={`${entry.raised_at}-${index}`}>
                        {entry.raised_at
                          ? formatDateTimeInMalaysia(entry.raised_at)
                          : 'Unknown'}
                        {entry.raised_by_name ? ` · ${entry.raised_by_name}` : ''}
                      </p>
                    ))}
                  </TooltipContent>
                </Tooltip>
              )}
            </span>
          ) : (
            <Muted>Unknown</Muted>
          );
        },
      },
      // No Confirmed column (S1, AC-1.5): there is no manual confirm left to report on,
      // and the two facts that column existed to carry - a rejection and a settle-in-place
      // Was/Now - render in the qty cell above instead.
    ];
    // REV-S6/S1: a redirected row reads muted via the DataGrid's own `rowClassName`
    // (OrderInquiriesClient.tsx), not a per-cell wrapper here - a `display: contents`
    // wrapper has no box, so `opacity-60` on it never applies. The select column was
    // never touched by that wrapper either: a redirected row still has to be tickable
    // like any other (AC-A1..: only `cancelled` is ever excluded from selection).
    return columns;
  }, [selectable]);
}
