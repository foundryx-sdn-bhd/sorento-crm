'use client';

import * as React from 'react';
import { Check, Undo2 } from 'lucide-react';
import { cn } from '@/lib/utils';
import { Button } from '@/components/ui/button';
import { PillOverflow, type PillItem } from '@/components/common/PillOverflow';
import { bucketLabelText } from '../../_shared/lib/fulfilmentBoard';
import { canQuickSave } from '../../_shared/lib/boardAmend';
import { toMinor } from '../../_shared/lib/supplyComposition';
import { BoardChangeTable } from './BoardChangeTable';
import { BoardDecidedMarker, decidedRevisions } from './BoardDecidedMarker';
import { isPreMarkOnly } from './BoardDecisionPill';
import { SupplyBar } from '../../_shared/components/SupplyBar';
import {
  COLOURS,
  LABELS,
  cellSupply,
  dominant,
  dominantText,
} from '../../_shared/lib/supplyVocabulary';
import type { BoardChangeAnnotation } from '../../_shared/lib/boardChangeAnnotations';
import type {
  BoardAxisRow,
  BoardCell,
  BoardContribution,
  BoardDateBucket,
  BoardDraft,
} from '../../_shared/types/fulfilmentPlanning.types';
import { OrderInquiryRejectedMark } from '../../_shared/components/OrderInquiryRejectedBadge';
import { purchasingRefusalTitle } from '../../_shared/lib/orderInquiryAck';

/**
 * Pending changes grouped by the LINE they happened to, in the order the lines first appear
 * (AC-D5). The planning line first, then the sales order's own address for a line nobody has
 * adopted - the same pair `FulfilmentBoardListView`'s own `changeIcons` keys a row by.
 */
function changesByLine(
  annotations: BoardChangeAnnotation[],
): BoardChangeAnnotation[][] {
  const byLine = new Map<string, BoardChangeAnnotation[]>();
  for (const annotation of annotations) {
    const key =
      annotation.projectLineId ??
      `${annotation.soNumber}|${annotation.lineNo}|${annotation.itemCode}`;
    const held = byLine.get(key);
    if (held) held.push(annotation);
    else byLine.set(key, [annotation]);
  }
  return [...byLine.values()];
}

const PRODUCT_COL = 'w-[190px] min-w-[190px] max-w-[190px]';
/**
 * A FLOOR, not a fixed width. With `table` at `w-full` and `table-layout` left at its browser
 * default (AUTO, never `table-fixed`), a date column below this width never happens - it can
 * only grow, splitting whatever space the product column did not take. Two selected weeks fill
 * the bordered container instead of sitting in a third of it with the rest blank; twenty weeks
 * push the table past the container's width and the container's own `overflow-auto` takes over,
 * exactly as it did before.
 */
const DATE_COL = 'min-w-[150px]';

/**
 * Two paint layers, and every pinned cell names the one it is on. A cell pinned on BOTH axes
 * (the "Product" corner) has to beat a cell pinned on one, because with equal z-index the
 * winner is whichever the DOM happened to put last rather than whichever a reader expects.
 * Same rule, and the same reason, as the delivery-schedule matrix.
 *
 * Both tokens (css/config.reui.css) sit below --z-header/--z-sidebar: a pinned cell has to
 * beat the columns scrolling under it, but never the app shell - the collapsed sidebar's
 * hover flyout has to render over the frozen Product column, not under it.
 */
const Z_PINNED = 'z-(--z-sticky-content)';
const Z_CORNER = 'z-(--z-sticky-content-corner)';

/**
 * A pinned cell has to be OPAQUE. `bg-destructive/10` is ninety percent transparent, so a
 * quantity scrolling underneath a pinned header shows straight through it and reads as a
 * number appearing in the header. `color-mix` gives the same tint with actual paint on it, and
 * is also the only way to tint these tokens: Tailwind v4 resolves them to `oklch(...)`, so
 * `hsl(var(--destructive) / 0.1)` is dropped by the browser as invalid.
 */
const PAST_HEADER_BG =
  'bg-[color-mix(in_oklab,var(--destructive)_12%,var(--muted))]';
const NO_DATE_BG = 'bg-[color-mix(in_oklab,var(--foreground)_6%,var(--muted))]';

/*
 * NO BODY TINT FOR A PAST BUCKET. The column header carries it and says "Already past", which
 * is where the fact belongs; on the cells it collided with the supply bar, where rose means Buy
 * and nothing else (PLAN section C, AC-C4). One rose on a cell, one meaning.
 */

/**
 * The planning board: DATE BUCKETS across the top, PRODUCTS down the side, each cell the
 * quantity of that product owed by that date across every selected sales order (PLAN 13).
 *
 * The axis words are `dateBuckets` and `rows` deliberately. The delivery-schedule matrix calls
 * a PRODUCT a `column` - its API's word, kept on purpose even after that grid was transposed to
 * this same orientation - so borrowing its vocabulary here would leave two grids in the same
 * module using one word for opposite things.
 *
 * The vertical axis is no longer always products: it pivots to sales order, customer or project
 * (the captain: "how about if we want vertical is sales order, is customer, is project"). Each
 * row carries a `key` that is an id and a `label` that is what the reader sees, so two customers
 * with one name are two rows and neither shows an id.
 *
 * NOT a DataGrid, and this is the same carve-out that file documents: here the COLUMNS ARE
 * DATA. There is one per date bucket, there are as many as the selection needs, and no column
 * config, sort or resize applies to them. What DataGrid would otherwise give us is solved
 * explicitly, and those three obligations are the price of the carve-out:
 *
 * - the whole table scrolls INSIDE this container, so the page body never scrolls sideways;
 * - the product column is sticky and the header row is sticky, both opaque, so a reader
 *     eight buckets in still knows which product they are on;
 * - the table is `w-full` so a selection of two weeks fills the bordered container rather
 *     than sitting in a third of it with the rest blank (measured live), the product column
 *     keeps a fixed width because it is not part of what should stretch, and the date columns
 *     carry only a `min-w` floor so they grow evenly into whatever is left. `table-layout` is
 *     never `table-fixed`, which overlaps its columns the moment content exceeds the declared
 *     width - a wide selection still overflows past the floor and the container's own
 *     `overflow-auto` takes it from there, unchanged from before.
 *
 * A BLANK CELL IS NOT A ZERO. It means no selected order owes that product by that date, so it
 * renders blank and stays blank.
 *
 * THE PAST IS TINTED ON ITS HEADER, NOT MERGED. Every dated bucket is a real date, however far
 * back, and the server's `is_past` is the only thing this grid colours a HEADER on. The board
 * used to carry one aggregate Overdue column and it collapsed a whole selection into it - 160 of
 * 160 lines, with their dates gone. The cost of the change is columns, and the board pays it: it
 * spans years, so the horizontal scroll below is load-bearing rather than a nicety.
 */
export function FulfilmentBoardMatrix({
  dateBuckets,
  rows,
  rowHeader,
  cells,
  draft,
  annotations,
  onOpenCell,
  onDecideMany,
  onUndoMany,
}: {
  dateBuckets: BoardDateBucket[];
  /** Whatever the vertical axis is: products, sales orders, customers or projects. */
  rows: BoardAxisRow[];
  /** What the corner cell calls them. */
  rowHeader: string;
  cells: BoardCell[];
  /**
   * THE panel's draft, not a set of keys derived from it: the cell counts what is ticked AND
   * colours itself by what was ticked, and two readings of one draft would drift.
   */
  draft: BoardDraft;
  /**
   * What a re-uploaded book did to the lines in each cell, keyed the same way the cells are
   * (AC-P3-2). Empty on every board opened without a `batch`, which is nearly all of them.
   */
  annotations?: Map<string, BoardChangeAnnotation[]>;
  onOpenCell: (cell: BoardCell) => void;
  /**
   * D15: a cell's own "save everything still eligible in this cell" icon posts through this -
   * the same quiet-bulk path the board-wide "Save all suggested" button uses, one toast for
   * the whole press.
   */
  onDecideMany: (keys: string[]) => Promise<{ saved: number; failed: number }>;
  /** D15: the cell's own Undo, for a cell holding only drafted lines and nothing left to save. */
  onUndoMany: (keys: string[]) => Promise<{ saved: number; failed: number }>;
}) {
  // Keyed by the cell's ROW KEY, which is the item code on the product axis and an id on the
  // pivoted ones - two customers sharing a name must not share a row.
  const byKey = React.useMemo(() => {
    const map = new Map<string, BoardCell>();
    for (const cell of cells) {
      map.set(`${cell.row_key ?? cell.item_code}|${cell.bucket_key}`, cell);
    }
    return map;
  }, [cells]);

  return (
    <div
      data-testid="fulfilment-board-matrix"
      className="relative max-h-[70vh] w-full overflow-auto overscroll-x-contain rounded-lg border border-border"
    >
      <table className="w-full border-separate border-spacing-0 text-xs">
        <thead>
          <tr>
            <th
              scope="col"
              className={cn(
                PRODUCT_COL,
                Z_CORNER,
                'sticky left-0 top-0 border-b border-e border-border bg-muted px-2 py-2 text-start align-bottom font-medium',
              )}
            >
              {rowHeader}
            </th>
            {dateBuckets.map((bucket) => (
              <th
                key={bucket.key}
                scope="col"
                data-bucket={bucket.key}
                data-past={String(Boolean(bucket.is_past))}
                className={cn(
                  DATE_COL,
                  Z_PINNED,
                  'sticky top-0 border-b border-e border-border px-2 py-2 text-start align-bottom font-medium',
                  bucket.kind === 'no_date'
                    ? NO_DATE_BG
                    : bucket.is_past
                      ? PAST_HEADER_BG
                      : 'bg-muted',
                )}
              >
                <span
                  className="block truncate"
                  title={bucketLabelText(bucket.label)}
                >
                  {bucketLabelText(bucket.label)}
                </span>
                {(bucket.kind === 'no_date' || bucket.is_past) && (
                  <span className="block truncate text-[11px] font-normal text-muted-foreground">
                    {bucket.kind === 'no_date' ? 'Not stated' : 'Already past'}
                  </span>
                )}
              </th>
            ))}
          </tr>
        </thead>

        <tbody>
          {rows.map((product) => (
            <tr key={product.key}>
              <th
                scope="row"
                className={cn(
                  PRODUCT_COL,
                  Z_PINNED,
                  'sticky left-0 border-b border-e border-border bg-background px-2 py-1.5 text-start font-medium',
                )}
              >
                {/* The label alone. A product's name is searchable but not printed here: the
                    first column is sticky and every character of it costs width on a board
                    that already spans years. */}
                <span
                  className="block truncate"
                  title={product.description || product.label}
                >
                  {product.label}
                </span>
              </th>

              {dateBuckets.map((bucket) => {
                const cell = byKey.get(`${product.key}|${bucket.key}`);
                return (
                  <td
                    key={bucket.key}
                    data-cell={`${product.key}|${bucket.key}`}
                    className={cn(
                      DATE_COL,
                      'border-b border-e border-border p-0 align-top',
                    )}
                  >
                    {cell ? (
                      <div className="relative">
                        <BoardCellButton
                          cell={cell}
                          draft={draft}
                          onOpen={() => onOpenCell(cell)}
                        />
                        {/* D15: the cell's own save-everything / undo-everything icon, a
                            SIBLING of the button rather than nested inside it - a button
                            inside a button is invalid HTML the browser reflows out of it,
                            which is why this is absolutely positioned in the cell's own
                            corner instead. */}
                        <BoardCellQuickAction
                          cell={cell}
                          bucketLabel={bucketLabelText(bucket.label)}
                          draft={draft}
                          onDecideMany={onDecideMany}
                          onUndoMany={onUndoMany}
                        />
                        {/* What the re-uploaded book did to the lines in this cell
                            (AC-P3-2), as ONE hazard icon per changed line beside the
                            figure - the detail is a click away (AC-C9, owner feedback 13
                            September 2026: the inline table was "abit too big"). A SIBLING
                            of the button, never inside it: a button inside a button is
                            invalid HTML the browser reflows out of it. */}
                        {(annotations?.get(`${product.key}|${bucket.key}`) ?? []).length >
                        0 ? (
                          <div className="flex flex-wrap items-center gap-1 px-2 pb-1.5">
                            {/* ONE icon per changed LINE, not per pending batch row
                                (AC-D5, owner finding 22 Sep: "why so many warning
                                signs") - two rows that both moved the same line's date
                                are one warning about one line, and the lightbox behind
                                the icon lists them. A cell holding three DIFFERENT
                                lines still draws three icons: they are three lines. */}
                            {changesByLine(
                              annotations?.get(`${product.key}|${bucket.key}`) ?? [],
                            ).map((group) => (
                              <BoardChangeTable
                                key={group[0].rowId}
                                annotations={group}
                                compact
                                contribution={cell.contributions.find(
                                  (row) =>
                                    row.so_number === group[0].soNumber &&
                                    (row.so_line_no ?? row.line_no) === group[0].lineNo,
                                )}
                              />
                            ))}
                          </div>
                        ) : null}
                      </div>
                    ) : null}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/**
 * One cell: the quantity, then the source strip that answers "where will I need to source to
 * fulfil" without anybody having to click (journey step 3).
 *
 * A cell is a button because clicking it is the whole interaction, and a div with an onClick
 * is not reachable from a keyboard.
 */
function BoardCellButton({
  cell,
  draft,
  onOpen,
}: {
  cell: BoardCell;
  draft: BoardDraft;
  onOpen: () => void;
}) {
  const decided = cell.contributions.filter((entry) =>
    Boolean(draft[entry.key]),
  ).length;
  // Where this cell's quantity is coming from: the DECISION on every line that has one, the
  // engine's proposal on the rest. Ticking Amend from Buy to the shared pool flips the bar
  // rose to sky before anything is confirmed, and clearing the tick puts it back.
  const supply = cellSupply(cell, draft);
  const lead = dominant(supply.segments);
  // Only the locations this cell's own lines name. `cell.locations` also carries the rest of
  // the sales agent's ownership group, which holds none of this cell's demand - listing those
  // here would read "BRW-BB 42 · MWH-BB 0 · DC1-BB 0" on a grid whose whole job is to be
  // scanned. Their stock position is the drill-down's answer, not this strip's.
  const stripLocations = cell.locations.filter(
    (entry) => toMinor(entry.qty) > 0,
  );
  const stripPills: PillItem[] = stripLocations.map((entry, index) => ({
    key: `${entry.location ?? 'no-location'}-${index}`,
    label: `${entry.location ?? 'No location'} ${entry.qty}`,
  }));
  const orders = new Set(cell.contributions.map((entry) => entry.so_number))
    .size;
  // Confirmed in the DATABASE, not ticked in the draft: the `decided` badge below already
  // counts the draft, and a cell whose supply is settled is a different statement.
  const confirmedRevisions = decidedRevisions(cell.contributions);
  // "71 lent to SO415472", one phrase per borrow, across every line in the cell.
  const lent = cell.contributions
    .flatMap((entry) => entry.lent_to ?? [])
    .map((row) => `${row.qty} lent to ${row.so_number ?? 'another order'}`)
    .join(' · ');
  // What purchasing REFUSED, on the line it came back to (AC-H6). PR 1622 (owner, mock v3):
  // the refusal is the ORDER INQUIRY's status, so the cell names the inquiry with the same
  // Rejected pill and info icon as the List view, one per refused inquiry, and the decision
  // half of the cell says nothing about it.
  const refusals = new Map<string, NonNullable<BoardContribution['order_inquiry']>>();
  for (const entry of cell.contributions) {
    const inquiry = entry.order_inquiry;
    if (inquiry?.ack_state !== 'rejected') continue;
    const key = inquiry.inquiry_no ?? entry.key;
    if (!refusals.has(key)) refusals.set(key, inquiry);
  }
  const label = `${cell.item_code}, ${cell.total_qty} across ${orders} sales order${
    orders === 1 ? '' : 's'
  }`;

  return (
    <button
      type="button"
      onClick={onOpen}
      aria-label={label}
      // THE WHOLE CELL is the click target (AC-3.1/3.8, the mockup's revision): the button
      // already fills the `td`, so what was missing was the AFFORDANCE, not a bigger hit
      // area. A ring on hover, restrained rather than the mockup's drop shadow (apple-design
      // "restraint" - one signal, not three), says the whole rectangle is pressable before
      // the pointer ever lands on the small text inside it.
      className="flex w-full cursor-pointer flex-col gap-0.5 px-2 py-1.5 text-start transition-colors hover:bg-accent hover:ring-1 hover:ring-inset hover:ring-primary/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40"
    >
      <span className="flex items-center gap-1.5">
        <span className="font-medium tabular-nums">{cell.total_qty}</span>
        <span className="text-[11px] text-muted-foreground">
          {orders === 1 ? '1 order' : `${orders} orders`}
        </span>
        <BoardDecidedMarker revisions={confirmedRevisions} />
      </span>

      {/* The source strip. One pill per distinct location, because one cell legitimately
          spans several: the location is the line's own, and lines from different orders do
          not have to agree about it (PLAN 13.7). S3b/R-J: pills, not a `·`-joined string, so
          a narrow column folds the rest behind "+N" instead of truncating it away entirely. */}
      {stripPills.length > 0 && (
        <PillOverflow
          items={stripPills}
          ariaLabel="Locations"
          testId={`cell-locations-${cell.row_key ?? cell.item_code}-${cell.bucket_key}`}
          renderPopover={(items: PillItem[]) => (
            <div className="space-y-1">
              {items.map((item) => (
                <p key={item.key}>{item.label}</p>
              ))}
            </div>
          )}
        />
      )}

      {/* The supply bar, and the dominant kind in words under it. The words are there because
          a colour alone is not a label, and short because the column is 150px wide. */}
      <SupplyBar
        segments={supply.segments}
        decided={supply.decided}
        labels={LABELS}
        colours={COLOURS}
      />
      {lead ? (
        <span
          data-testid="cell-supply-lead"
          className={cn(
            'block truncate text-[11px] font-medium',
            COLOURS[lead.kind].text,
          )}
        >
          {dominantText(supply.segments)}
        </span>
      ) : null}

      {/* Purchasing refused an instruction for one of these lines (AC-RB1, PR 1622 mock v3):
          the inquiry number with the Rejected pill and info icon, the sentence (who, when,
          why) in the title. Tapping the cell opens the drill-down, whose Order inquiry column
          carries the same badge with its popover for a screen with no hover. */}
      {Array.from(refusals.entries()).map(([key, inquiry]) => {
        const sentence = purchasingRefusalTitle(inquiry);
        return (
          <span
            key={key}
            data-testid="cell-rejected"
            className="flex min-w-0 items-center gap-1 text-[11px]"
            title={sentence}
            aria-label={`${inquiry.inquiry_no ?? 'Order inquiry'}: ${sentence}`}
          >
            {inquiry.inquiry_no ? (
              <span className="min-w-0 truncate tabular-nums">{inquiry.inquiry_no}</span>
            ) : null}
            <OrderInquiryRejectedMark />
          </span>
        );
      })}

      {/* What another sales order borrowed OFF the lines in this cell (AC-L6). A borrow was
          visible on the taking side and invisible on the giving side, so the agent whose
          stock moved found out when the delivery did not. */}
      {lent ? (
        <span
          data-testid="cell-lent-out"
          className="block truncate text-[11px] font-medium text-amber-700"
          title={lent}
        >
          {lent}
        </span>
      ) : null}

      <span className="flex flex-wrap gap-1">
        {cell.unplannable_count > 0 && (
          <span className="rounded bg-destructive/10 px-1 text-[10px] font-medium text-destructive">
            {`${cell.unplannable_count} need${cell.unplannable_count === 1 ? 's' : ''} a location`}
          </span>
        )}
        {decided > 0 && (
          <span className="rounded bg-emerald-100 px-1 text-[10px] font-medium text-emerald-800">
            {`${decided}/${cell.contributions.length} decided`}
          </span>
        )}
      </span>
    </button>
  );
}

/**
 * A cell's own save-everything / undo-everything icon (D15), pinned to the cell's own corner.
 *
 * A SIBLING of `BoardCellButton`, never a child of it: the button already fills the `td`, and
 * a button nested inside a button is invalid HTML the browser reflows out of - the same reason
 * the change-annotation table beside it is a sibling too. `event.stopPropagation()` alone would
 * not have been enough on its own: without the sibling structure the click still bubbles through
 * an ancestor `<button>`'s own click handler once the browser has un-nested the markup.
 *
 * SAVE wins over UNDO when a cell holds both an eligible line and a drafted one (the fact sheet's
 * own rule): the drafted line's own Undo is still reachable inside the dialog, and a cell-wide
 * verb has to pick one action per press.
 */
function BoardCellQuickAction({
  cell,
  bucketLabel,
  draft,
  onDecideMany,
  onUndoMany,
}: {
  cell: BoardCell;
  bucketLabel: string;
  draft: BoardDraft;
  onDecideMany: (keys: string[]) => Promise<{ saved: number; failed: number }>;
  onUndoMany: (keys: string[]) => Promise<{ saved: number; failed: number }>;
}) {
  const eligible = cell.contributions.filter((entry) => canQuickSave(entry, draft));
  // No pre-mark in this count (PLAN-board-change-proposed-pill, fix round): a pre-mark has
  // nothing saved on the server yet, so `onUndoMany` -> `deleteLineDraft` on it is a DELETE
  // against a row that was never written - it tolerates the 404, but the pre-mark's own key
  // never gets re-seeded (`preMarkedBatchIds` already saw this batch), so the line silently
  // drops out of Confirm (N) until the page reloads.
  const drafted = cell.contributions.filter(
    (entry) => Boolean(draft[entry.key]) && !isPreMarkOnly(entry, draft[entry.key] ?? null),
  );

  if (eligible.length > 0) {
    const keys = eligible.map((entry) => entry.key);
    return (
      <Button
        type="button"
        mode="icon"
        variant="ghost"
        size="sm"
        className="absolute end-1 top-1 z-10 size-6 rounded-full bg-background/90 shadow-sm"
        title="Save cell as suggested"
        aria-label={`Save ${cell.item_code} ${bucketLabel} as suggested`}
        onClick={(event) => {
          event.stopPropagation();
          void onDecideMany(keys);
        }}
      >
        <Check className="size-3.5" aria-hidden />
      </Button>
    );
  }

  if (drafted.length > 0) {
    const keys = drafted.map((entry) => entry.key);
    return (
      <Button
        type="button"
        mode="icon"
        variant="ghost"
        size="sm"
        className="absolute end-1 top-1 z-10 size-6 rounded-full bg-background/90 shadow-sm"
        title="Undo"
        aria-label={`Undo ${cell.item_code} ${bucketLabel}`}
        onClick={(event) => {
          event.stopPropagation();
          void onUndoMany(keys);
        }}
      >
        <Undo2 className="size-3.5" aria-hidden />
      </Button>
    );
  }

  return null;
}
