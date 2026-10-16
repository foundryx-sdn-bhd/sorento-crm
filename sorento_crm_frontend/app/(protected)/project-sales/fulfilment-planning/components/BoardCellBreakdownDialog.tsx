'use client';

import * as React from 'react';
import Link from 'next/link';
import { Check, ChevronDown, ChevronRight, ExternalLink, Info, X } from 'lucide-react';
import { ColumnDef, RowSelectionState } from '@tanstack/react-table';
import { ConsignmentBadge } from '@/app/(protected)/scm/lib/docType';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Badge } from '@/components/ui/badge';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { DataGridColumnHeader } from '@/components/ui/data-grid-column-header';
import { buildSelectColumn } from '@/components/ui/data-grid-select-column';
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from '@/components/ui/tooltip';
import { StatCard } from '@/components/scm/StatCard';
import { ListSearchInput } from '@/components/common/ListSearchInput';
import { PillOverflow, type PillItem } from '@/components/common/PillOverflow';
import {
  isSearchInFlight,
  useDebouncedSearch,
} from '@/hooks/useDebouncedSearch';
import { cn } from '@/lib/utils';
import { formatDateInMalaysia } from '@/lib/helpers';
import { spoDetailHref, spoNumberFromLabel } from '@/lib/spo-detail';
import { PanelDataGrid } from '@/components/common/PanelDataGrid';
import { DecisionTrailButton } from '../../_shared/components/DecisionTrailButton';
import { OrderInquiryStatePill } from '../../_shared/components/OrderInquiryVerbPill';
import { OrderInquiryRejectedBadge } from '../../_shared/components/OrderInquiryRejectedBadge';
import {
  PILL_TONE,
  SHORT_LABELS,
  boardOrderInquiryWord,
  contributionInquiryDecision,
  contributionSuggestion,
  decisionBreakdown,
  movesOf,
  movesText,
  rowOf,
  rowText,
  suggestionBreakdown,
  takenByLocation,
  takenKindsByLocation,
} from '../../_shared/lib/supplyVocabulary';
import type { SuggestionRow } from '../../_shared/lib/supplyVocabulary';
import { LADDER_VERSION } from '../../_shared/lib/supplyVocabulary';
import { fromMinor, toMinor } from '../../_shared/lib/supplyComposition';
import { canQuickSave } from '../../_shared/lib/boardAmend';
import { soLineLabel } from '../../_shared/lib/fulfilmentBoard';
import { BoardDecisionPill, isPreMarkOnly } from './BoardDecisionPill';
import { BoardLineDecisionPanel } from './BoardLineDecisionPanel';
import { BoardVerdictActions } from './BoardVerdictActions';
import { BoardTrailPopover, ItemFlagChips } from './BoardTrailPopover';
import {
  UnsavedDecisionPrompt,
  useDecisionRowExpansion,
} from './decisionRowExpansion';
import { CellStockTable } from './CellStockTable';
import type {
  BoardCell,
  BoardContribution,
  BoardDecision,
  BoardDraft,
  BoardLadderOption,
  BoardSource,
  CellStockTableHandle,
  StockDocumentMatch,
  StockDonorMatch,
} from '../../_shared/types/fulfilmentPlanning.types';
import type { SupplyKind } from '../../_shared/lib/supplyVocabulary';

/**
 * The breakdown behind one cell, and the decision on it (PLAN 13, journey step 4).
 *
 * A TABLE on the shared DataGrid, and the captain's reason for it is the whole design: "this
 * needs to be more table based instead of card based, so it is easier to see, and you need to
 * show me the SO order quantity, outstanding quantity also in the table ... then need to
 * show summary row whenever relevant". Ten lines as ten cards is a scroll; ten lines as ten rows
 * is a comparison, which is what a planner is actually doing here.
 *
 * The earlier version of this file argued for cards on the grounds that a row carries a
 * two-line explanation and a balance line. That argument does not survive the ask: the
 * per-row reasoning lives in the `title` of the cell that shows the composition, which is the
 * same `truncate` + `title` contract every other grid in this repo uses for long text.
 *
 * ONE VOCABULARY: what is still to go out is **outstanding**, everywhere on this screen. It
 * used to be "owed" in a column header, in a balance equation at the top and in a column of
 * the stock table, while the sales-order screens next door said "outstanding" for the same
 * figure - and a reader cannot tell a deliberate distinction from an accidental one.
 *
 * Approve / amend / reject are a ROW ACTION, and they write into the board's DRAFT, not into
 * the database (13.4): the decision that is persisted is still the whole sales order's, and the
 * order is what commits. Nothing here claims a cell committed anything.
 *
 * LAYOUT IS LOad-BEARING. The dialog is a flex column with its own max height; the body is the
 * only scrolling region and the footer is its SIBLING. Measured before the fix: at a 560px-tall
 * window the footer painted over the row's Approve button, so `document.elementFromPoint` at the
 * button's own centre returned the footer, and a planner on a laptop could not decide anything
 * at all. A modal whose action is unreachable is a modal that does not work (PRINCIPLES:
 * "modals are scrollable so the submit button is reachable").
 */
export function BoardCellBreakdownDialog({
  cell,
  bucketLabel,
  draft,
  poolSharePct,
  onDecide,
  onDecideMany,
  onClose,
}: {
  cell: BoardCell;
  bucketLabel: string;
  draft: BoardDraft;
  /**
   * The board's own `pool_share_pct` (LADDER v8, R-K), passed through to the Stock tab so
   * its site-pool SUBTOTAL applies the tenant's real policy rather than a constant. Every
   * pool ROW arrives with the server's own `available_for_project` and needs nothing here.
   */
  poolSharePct?: number;
  onDecide: (key: string, decision: BoardDecision | null) => Promise<boolean> | void;
  /**
   * D15: the quiet-bulk path "Approve selected" and "Save all suggested" both post through -
   * each key's own suggestion, one toast for the whole press, rather than D14's N separate
   * "Line N saved" toasts.
   */
  onDecideMany: (keys: string[]) => Promise<{ saved: number; failed: number }>;
  onClose: () => void;
}) {
  // NO RANK, ANYWHERE ON THIS TABLE (R8, the captain 27 Aug: "rank goes"). Under ladder v4
  // availability belongs to the ownership GROUP and rank only decides the order lines are
  // served in, so a score in a column beside a quantity read as a property of that quantity.
  // The sentence that explained a flat score went with the column it explained.
  // Decided in the draft OR decided in the database: a line an active decision covers is as
  // decided as a line the planner has just approved, and counting only the draft made a cell of
  // confirmed lines read "0 decided".
  const decided = cell.contributions.filter(
    (entry) => Boolean(draft[entry.key]) || entry.covered,
  ).length;
  // #1362: every line of the cell has nothing left to plan (delivered after a decision was
  // saved on it). The drawer says so, with what was delivered and when it was due, instead
  // of a suggestion answering a quantity the line no longer has.
  const fulfilledLines =
    cell.contributions.length > 0 &&
    cell.contributions.every((entry) => Number(entry.qty) === 0)
      ? cell.contributions
      : [];
  // The item flags are per item; a covered or unplannable line carries null, so the first
  // line the ladder walked speaks for the cell. A cell holding several products (a pivoted
  // axis) states none: one item's verdict must not be pinned on another's.
  const flaggedContribution = cell.contributions.some(
    (entry) => entry.item_code !== cell.item_code,
  )
    ? null
    : (cell.contributions.find((entry) => entry.item_flags) ?? null);
  /**
   * Does this cell hold more than one product? On the product axis it never does - the cell IS
   * a product - and on a pivoted axis it routinely does.
   */
  const multiProduct = React.useMemo(
    () => new Set(cell.contributions.map((entry) => entry.item_code)).size > 1,
    [cell.contributions],
  );
  /** What the ladder proposes for the whole cell, by kind of source. */
  const suggestion = React.useMemo(() => suggestionBreakdown(cell), [cell]);
  /**
   * What was DECIDED for it, in the same rows and the same words (AC-D3).
   *
   * Empty until somebody decides something - confirmed on the order, or ticked into this
   * session's draft - and the card is not rendered then: a Decision card reading nothing
   * claims a decision was taken to do nothing.
   */
  const decision = React.useMemo(
    () => decisionBreakdown(cell, draft),
    [cell, draft],
  );
  /**
   * What has to physically MOVE for that decision, before Approve is pressed (section E).
   *
   * Derived from the same decision the card above renders, so the planner sees the
   * transfers their tick is about to raise rather than discovering them on another page.
   */
  const moves = React.useMemo(
    () => movesText(movesOf(cell, draft)),
    [cell, draft],
  );
  /**
   * How much the cell draws from each location, for the Taken column of the table above
   * (AC-B3). The decision when there is one and the suggestion otherwise, which is the same
   * switch the cell's colour bar uses.
   */
  const taken = React.useMemo(
    () => takenByLocation(cell, draft),
    [cell, draft],
  );
  /**
   * The SAME draw, kept apart by kind (S3b, R-J), so the Taken cell can show a pill per part
   * rather than folding a Reserve and a borrow at one location into one number.
   */
  const takenKinds = React.useMemo(
    () => takenKindsByLocation(cell, draft),
    [cell, draft],
  );
  /**
   * The lines this drawer is planning, for the documents panel under a location row: their
   * rows are tagged there, so a planner reading twenty other people's documents can see which
   * claim is their own (R5). CORE line ids, which is what the drill-down is addressed by.
   */
  const askingLineIds = React.useMemo(
    () =>
      cell.contributions
        .map((contribution) => contribution.line_id)
        .filter((value): value is string => Boolean(value)),
    [cell.contributions],
  );
  /**
   * Did ANY contributing line record what the engine suggested?
   *
   * A revision frozen before `proposed_components` existed (AC-D1) recorded none, and an
   * empty Suggestion card would then read as "the engine proposed nothing for this" - which
   * is a claim about the ladder rather than about the record. Verified live on SO324132 rev
   * 1, whose four lines all predate the field.
   */
  /**
   * Was this suggestion composed by a ladder that no longer runs?
   *
   * A frozen proposal outlives the rule that made it, and its sentences say so - "MWH-IB
   * has 30 available in the IB group" is v3 reading ONE warehouse's availability, which
   * under v4 is not a reading anybody makes, and v5 has no Incoming rung for a sentence
   * about an SPO to belong to. Shown as a short label on the card's own title rather than a
   * sentence beside it: what a planner needs is to know they are looking at history, and a
   * paragraph explaining ladder versions is a feature explanation in the UI.
   *
   * ONLY A DECIDED LINE CAN CARRY IT (AC-V8). An undecided line shows the LIVE suggestion,
   * which is today's answer by definition, and this used to appear on those too: the test
   * was "no `ladder` key", the backend stamped the key on neither the live nor the frozen
   * source, so every line on the board read as history.
   */
  const suggestionIsStale = React.useMemo(
    () =>
      cell.contributions.some(
        (entry) =>
          entry.covered &&
          (entry.proposed?.components?.some(
            (part) => part.ladder !== LADDER_VERSION,
          ) ??
            false),
      ),
    [cell.contributions],
  );
  const suggestionRecorded = React.useMemo(
    () =>
      cell.contributions.some(
        (entry) => contributionSuggestion(entry) !== null,
      ),
    [cell.contributions],
  );
  /**
   * Which row is open, ONE at a time (C3/C5): two open editors is two half-decisions, and a
   * screen of them is a scroll rather than a comparison. The SAME state the List view
   * keeps, so both readings of the board ask the same question before discarding an edit.
   */
  const expansion = useDecisionRowExpansion();
  const { expanded, setExpanded, openKey, setDirty, requestRow, requestClose } =
    expansion;

  /**
   * Open this line's decision panel, and only open it (AC-B10): the pencil is not a toggle -
   * a planner who presses it on the row that is already open asked to see the panel, and
   * closing it under them would read as the press having missed. Through `requestRow`, so
   * this table's one-row-at-a-time rule and its unsaved-work question both still apply.
   */
  const openRow = React.useCallback(
    (key: string) => {
      if (openKey === key) return;
      requestRow(key);
    },
    [openKey, requestRow],
  );
  const [rowSelection, setRowSelection] = React.useState<RowSelectionState>({});

  /**
   * The line whose STOCK POSITION the table above is showing (R1/B1).
   *
   * Every figure in that table is netted of ONE line's own quantity, because the ladder's
   * offer is `max(group net + that line's open quantity, 0)`; a cell holding two lines has
   * two answers, and a table averaging them would contradict both suggestions. So it follows
   * the row the planner opened, and falls back to the first contribution - which is the
   * server's own `cell.locations` and, on the overwhelming majority of cells, the only line
   * there is.
   */
  const shownContribution = React.useMemo(
    () =>
      cell.contributions.find((entry) => entry.key === openKey) ??
      cell.contributions[0],
    [cell.contributions, openKey],
  );
  const shownLocations = shownContribution?.locations ?? cell.locations;

  /**
   * The lightbox's own navigation (S3, PLAN-scm-planning-feedback-31aug): a jump to "This
   * line", a donor or an incoming document is driven through this handle rather than through
   * state here, because the row it scrolls to lives inside a query `CellStockTable` and
   * `StockDocumentsPanel` own, not inside this dialog.
   */
  const stockTableRef = React.useRef<CellStockTableHandle>(null);

  /**
   * Every donor and every incoming document the SHOWN line's own suggestion names, if any
   * (AC-3.3/3.4/3.13). A cell can carry more than one of EACH - a step-2 combine draws from
   * several donors on one line (R35) - so both are lists, never a single `.find()` (review
   * round, S3): the old single pick meant a second donor's own link in the sentence jumped to
   * the FIRST donor's row instead of its own.
   *
   * A source can carry a donor AND a document together - "Borrow ... (SPO x) from SO397460"
   * names an SPO and the order waiting on it in one sentence - so the two lists are read
   * independently rather than as alternatives.
   *
   * MEMOISED on the underlying source lists, not left as bare object literals (review round,
   * S3): an unmemoised `donorMatch`/`documentMatch` got a fresh identity on every render of
   * this dialog, including the ones typing in the sticky search causes, which fed
   * `StockDocumentsPanel`'s own `rows` memo a new `donor`/`documentInfo` reference on every
   * keystroke and re-ran its jump effect - see that component's own module doc for the other
   * half of the fix.
   */
  const donorSources = React.useMemo(
    () => (shownContribution?.sources ?? []).filter((source) => source.donor_so_number),
    [shownContribution],
  );
  const documentSources = React.useMemo(
    () => (shownContribution?.sources ?? []).filter((source) => source.supply_document),
    [shownContribution],
  );
  // The toolbar's single "document" button and badge can only ever point at ONE document, so
  // it takes the first one that could actually BE a row this drill matches: a PO reaches a
  // project line by a link, never by sitting as its own document in the stock ledger this
  // drill reads, so a historical PO snapshot (`supply_document` starting "PO ") can never
  // match a row here - offering a button for it would be a jump to nowhere.
  const jumpableDocumentSource = React.useMemo(
    () =>
      documentSources.find((source) => isJumpableDocument(source.supply_document)) ?? null,
    [documentSources],
  );
  const donorMatches: StockDonorMatch[] = React.useMemo(
    () =>
      donorSources.map((source) => ({
        soNumber: source.donor_so_number as string,
        lineId: source.donor_core_line_id,
        location: source.location,
      })),
    [donorSources],
  );
  const documentMatch: StockDocumentMatch | null = React.useMemo(
    () =>
      jumpableDocumentSource
        ? {
            spoNumber: jumpableDocumentSource.supply_document as string,
            location: jumpableDocumentSource.location,
          }
        : null,
    [jumpableDocumentSource],
  );

  /** AC-3.5: the sticky toolbar's search, filtering the Stock tab's expanded documents. */
  const stockSearch = useDebouncedSearch();
  /** AC-3.5: the Contributing lines tab's own search - a separate box, a separate list. */
  const linesSearch = useDebouncedSearch();

  /**
   * AC-3.1: opening a cell lands the lightbox at "This line" by default - own-location
   * expanded, scrolled, flashed - with no intermediate expand-then-hunt step.
   *
   * `CellStockTable` itself raises this landing on its OWN mount (its module doc explains
   * why) rather than this effect calling it through the ref: the dialog's `<Tabs>` needs a
   * commit of its own before the "stock" panel's content actually mounts, so a mount-time
   * effect HERE would fire while `stockTableRef.current` is still null and never re-fire -
   * `cellKey` does not change between that first commit and the one that follows it. Kept as
   * the remount key so a stock expansion never survives from one cell to the next.
   */
  const cellKey = `${cell.row_key ?? cell.item_code}|${cell.bucket_key}`;

  const selectedKeys = React.useMemo(
    () => Object.keys(rowSelection).filter((key) => rowSelection[key]),
    [rowSelection],
  );

  /**
   * AC-3.5: what the Contributing lines search leaves standing - SO number, customer or
   * agent, case-insensitively, client-side. `cell.contributions` is already the whole cell,
   * so there is nothing to fetch a second time.
   */
  const filteredContributions = React.useMemo(() => {
    const needle = linesSearch.debouncedValue.trim().toLowerCase();
    if (!needle) return cell.contributions;
    return cell.contributions.filter((contribution) =>
      [
        contribution.so_number,
        contribution.customer_name,
        contribution.agent_code,
      ]
        .filter((value): value is string => Boolean(value))
        .some((value) => value.toLowerCase().includes(needle)),
    );
  }, [cell.contributions, linesSearch.debouncedValue]);

  /**
   * One verdict, applied to every ticked row.
   *
   * The captain's screenshot was eleven identical rows: deciding those one at a time is eleven
   * presses to say one thing. It writes into the same client draft a single decision does, so
   * nothing posts here and Confirm remains the only write.
   *
   * AMEND is deliberately not offered in bulk: an amendment is a quantity and a reason for ONE
   * line, and a single quantity applied to eleven different outstanding quantities is not a decision
   * anybody meant to make.
   */
  const decideSelected = React.useCallback(
    (decision: BoardDecision) => {
      for (const key of selectedKeys) onDecide(key, decision);
      setRowSelection({});
    },
    [selectedKeys, onDecide],
  );

  /**
   * APPROVE, ticked row by ticked row - the same loop `decideSelected` runs, but each key
   * gets ITS OWN suggestion rather than one decision applied to every row, because
   * "approved" is not one object across lines with different suggestions the way "rejected"
   * is.
   *
   * D11 is why this is the only approve verb here: a bare `{verdict: 'approved'}` carries no
   * composition, so a Save-untouched confirms nothing the engine proposed and the sales order
   * page reads it as Decided "-". An approval IS the suggested composition, which is exactly
   * what `BoardLineDecisionPanel`'s own untouched Save posts - so approving in bulk and
   * "saving as suggested" were one verb with two buttons, and this is the one.
   *
   * D15: posts through `onDecideMany` now, not a loop of `onDecide` calls - the composition is
   * still each key's own suggestion, computed the same way, but the board posts them quietly
   * and toasts once for the whole press instead of once per line.
   */
  const approveSelected = React.useCallback(() => {
    void onDecideMany(selectedKeys);
    setRowSelection({});
  }, [selectedKeys, onDecideMany]);

  /**
   * D14: every line in the cell a quick save could still touch - not covered (there is
   * nothing to approve on a confirmed line) and not already drafted (nothing a re-save would
   * change). No selection needed for this one; it is the cell's own "nothing here is worth a
   * second look" button.
   */
  const suggestibleContributions = React.useMemo(
    () => cell.contributions.filter((entry) => canQuickSave(entry, draft)),
    [cell.contributions, draft],
  );
  const saveAllSuggested = React.useCallback(() => {
    void onDecideMany(suggestibleContributions.map((entry) => entry.key));
  }, [suggestibleContributions, onDecideMany]);

  /**
   * The muted line under the title, in the shape the family's shell wants it (`PlanRowDialog`,
   * `StockDebtCellDialog`): the facts about the CELL that neither tab states.
   *
   * The ownership GROUP leads, because it is the pile step 1 draws from and nothing else on
   * this screen prints its name - the stock table only speaks up when there is none. The
   * quantity follows so the description is never empty, which it would be on a cell whose
   * group the server could not resolve, and Radix needs it to describe the dialog at all.
   */
  const context = [
    cell.location_group ? `${cell.location_group} group` : null,
    // "to plan", not "outstanding": `total_qty` sums what each line ASKS FOR, which since
    // the 14 Sep 2026 ruling is the plan quantity. Beside a column headed Outstanding
    // reading 2 on the same delivered line, "3 outstanding" was the one screen stating two
    // different numbers under one word.
    `${cell.total_qty} to plan`,
    `${decided} decided`,
  ]
    .filter(Boolean)
    .join(' · ');

  const columns = React.useMemo<ColumnDef<BoardContribution>[]>(
    () => [
      // The repo's own select column, the one the users list uses, so the header select-all and
      // its indeterminate state are not a second implementation.
      buildSelectColumn<BoardContribution>({
        // Already SAVED is out here for the same reason it is out of the list view and out
        // of "Save all suggested": a quick save would overwrite an amended draft with the
        // engine's own composition, which is the one thing a planner who amended it does
        // not want. Undo is how a saved row comes back into play.
        enableRow: (row) => canQuickSave(row.original, draft),
        disabledReason: (row) =>
          row.original.covered
            ? 'This line is already confirmed. Amend it to change what was decided.'
            : row.original.unplannable
              ? 'This line cannot be decided here: its sales order states no fulfilment location.'
              : isPreMarkOnly(row.original, draft[row.original.key] ?? null)
                ? // N-2 (reviewer, fix round 2): was "Open the row to decide it", which is
                  // no longer where the quickest answer is - the Verdict cell beside this
                  // box now takes or refuses the proposal in one press.
                  "This line's change is proposed, not saved. Accept or reject it in the Verdict cell."
                : draft[row.original.key]
                  ? 'Already saved. Undo it before saving it again.'
                  : undefined,
        rowLabel: (row) =>
          `Select ${row.original.so_number} ${soLineLabel(row.original).toLowerCase()}`,
      }),
      {
        id: 'so_number',
        accessorFn: (row) => row.so_number,
        header: ({ column }) => (
          <DataGridColumnHeader title="Sales order" column={column} />
        ),
        cell: ({ row }) => (
          <div className="flex min-w-0 items-start gap-1.5">
            {/* A state indicator, not a control: the WHOLE row toggles the decision panel,
                the same gesture reorder planning's group rows use. */}
            {row.getIsExpanded() ? (
              <ChevronDown
                className="mt-0.5 size-3.5 shrink-0 text-muted-foreground"
                aria-hidden
              />
            ) : (
              <ChevronRight
                className="mt-0.5 size-3.5 shrink-0 text-muted-foreground"
                aria-hidden
              />
            )}
            <div className="min-w-0">
              <div
                className="truncate text-sm font-medium"
                title={row.original.so_number}
              >
                {row.original.so_number}
              </div>
              <ConsignmentBadge docType={row.original.so_doc_type} size="xs" />
              <div className="truncate text-xs text-muted-foreground">
                {soLineLabel(row.original)}
              </div>
            </div>
          </div>
        ),
        // Labels the totals row under the first column, the way a spreadsheet labels its sum,
        // so the number below Outstanding needs no caption of its own.
        footer: () => <span className="text-muted-foreground">Total</span>,
        size: 130,
        minSize: 110,
        meta: {
          headerTitle: 'Sales order',
          // The decision, in the row (C3/C4). `DataGridTable` renders this full-width under
          // any row whose `getIsExpanded()` is true - the same mechanism reorder planning's
          // per-warehouse drill uses - so there is one expansion surface in this product
          // rather than a second one invented here.
          expandedContent: (contribution: BoardContribution) => (
            <BoardLineDecisionPanel
              contribution={contribution}
              decision={draft[contribution.key] ?? null}
              locations={contribution.locations ?? cell.locations}
              onDecide={(next) => onDecide(contribution.key, next)}
              onDirtyChange={setDirty}
            />
          ),
        },
      },
      // The product per line, ONLY when the cell holds more than one. On the product axis the
      // dialog's own title names it and every row repeats it, which is a column of one
      // repeated word on a table that is already wide; on a pivoted axis a sales-order row's
      // cell genuinely holds several products, and a list of lines that does not say which
      // product each one is cannot be read at all.
      ...(multiProduct
        ? [
            {
              id: 'item_code',
              accessorFn: (row: BoardContribution) => row.item_code,
              header: ({ column }) => (
                <DataGridColumnHeader title="Product" column={column} />
              ),
              cell: ({ row }) => {
                // The SAME chip, off the SAME helper, as the list view's Product cell
                // (AC-A7/AC-A8): one ladder, read in both places, so the two readings of
                // the board cannot say different things about one line's documents.
                const word = boardOrderInquiryWord(row.original.order_inquiry);
                return (
                  <span className="flex min-w-0 items-center gap-1.5 text-sm">
                    <span
                      className="block min-w-0 truncate"
                      title={row.original.item_code}
                    >
                      {row.original.item_code}
                    </span>
                    {word ? (
                      <Badge
                        size="sm"
                        appearance="light"
                        variant="secondary"
                        className="shrink-0"
                        title={word.title}
                      >
                        {word.word}
                      </Badge>
                    ) : null}
                  </span>
                );
              },
              size: 150,
              minSize: 110,
              meta: { headerTitle: 'Product' },
            } as ColumnDef<BoardContribution>,
          ]
        : []),
      {
        id: 'customer_name',
        accessorFn: (row) => row.customer_name ?? '',
        header: ({ column }) => (
          <DataGridColumnHeader title="Customer" column={column} />
        ),
        cell: ({ row }) => (
          <span
            className="block truncate text-sm"
            title={row.original.customer_name ?? ''}
          >
            {row.original.customer_name || 'Not recorded'}
          </span>
        ),
        size: 125,
        minSize: 105,
        meta: { headerTitle: 'Customer' },
      },
      {
        // Who sold it, so the planner knows who to phone (the captain: agent "useful
        // information" beside every line).
        id: 'agent_code',
        accessorFn: (row) => row.agent_code ?? '',
        header: ({ column }) => (
          <DataGridColumnHeader title="Agent" column={column} />
        ),
        cell: ({ row }) =>
          row.original.agent_code ? (
            <span
              className="block truncate text-sm"
              title={row.original.agent_label ?? row.original.agent_code}
            >
              {row.original.agent_code}
            </span>
          ) : (
            <span className="text-sm text-muted-foreground">Not stated</span>
          ),
        size: 75,
        minSize: 65,
        meta: { headerTitle: 'Agent' },
      },
      {
        id: 'project_label',
        accessorFn: (row) => row.project_label ?? '',
        header: ({ column }) => (
          <DataGridColumnHeader title="Project" column={column} />
        ),
        cell: ({ row }) => (
          <span
            className="block truncate text-sm"
            title={row.original.project_label ?? ''}
          >
            {row.original.project_label || 'Not named on the order'}
          </span>
        ),
        size: 115,
        minSize: 95,
        meta: { headerTitle: 'Project' },
      },
      // NO Ordered / Delivered COLUMNS (R8). Both are facts about the LINE rather than about
      // the decision, they read "Not stated" on most of this book, and between them they cost
      // 220px of a table that had to scroll sideways to reach the Decision. They are read in
      // the expanded row instead, beside the outstanding quantity they explain.
      {
        id: 'qty',
        accessorFn: (row) => outstandingOf(row),
        header: ({ column }) => (
          <DataGridColumnHeader title="Outstanding" column={column} />
        ),
        cell: ({ row }) => (
          <span className="block truncate text-sm font-medium tabular-nums">
            {outstandingOf(row.original)}
          </span>
        ),
        footer: () => (
          <span className="tabular-nums">
            {sumOf(cell.contributions, outstandingOf)}
          </span>
        ),
        size: 85,
        minSize: 70,
        meta: { headerTitle: 'Outstanding' },
      },
      {
        id: 'required_date',
        accessorFn: (row) => row.required_date ?? '',
        header: ({ column }) => (
          <DataGridColumnHeader title="Delivery date" column={column} />
        ),
        cell: ({ row }) => (
          <span className="block truncate text-sm tabular-nums">
            {row.original.required_date
              ? formatDateInMalaysia(row.original.required_date)
              : 'No date'}
          </span>
        ),
        size: 100,
        minSize: 90,
        meta: { headerTitle: 'Delivery date' },
      },
      {
        id: 'fulfilment_location',
        accessorFn: (row) => row.fulfilment_location ?? '',
        header: ({ column }) => (
          <DataGridColumnHeader title="Location" column={column} />
        ),
        cell: ({ row }) =>
          row.original.fulfilment_location ? (
            <span
              className="block truncate text-sm"
              title={row.original.fulfilment_location}
            >
              {row.original.fulfilment_location}
            </span>
          ) : (
            <span className="text-sm text-destructive">No location</span>
          ),
        size: 90,
        minSize: 80,
        meta: { headerTitle: 'Location' },
      },
      {
        id: 'sources',
        accessorFn: (row) => row.sources.map((source) => source.kind).join(' '),
        header: ({ column }) => (
          <DataGridColumnHeader title="Sourced from" column={column} />
        ),
        cell: ({ row }) => {
          const contribution = row.original;
          // Merged per KIND (the ladder v8 SupplyKind, off `rowOf`) AND location, in the
          // order the ladder drew them - one pill per merged source (S3b, R-J). Question 1
          // hands over TWO components at one location whenever part of the group's offer is
          // on the water (a `reserve` off the floor and a `timely_spo` off the SPO): both
          // read "Own", so an unmerged strip printed the same word twice with two
          // quantities, which reads as a defect rather than as one draw in two forms.
          const merged: {
            kind: SupplyKind | null;
            label: string;
            at: string;
            minor: number;
            representative: BoardSource;
          }[] = [];
          for (const source of contribution.sources) {
            const kind = rowOf(source, contribution.fulfilment_location);
            const label = kind ? SHORT_LABELS[kind] : 'Cannot be sourced';
            const at = sourceAt(source);
            const seen = merged.find((m) => m.label === label && m.at === at);
            if (seen) seen.minor += toMinor(source.qty);
            else merged.push({ kind, label, at, minor: toMinor(source.qty), representative: source });
          }
          const pills: PillItem[] = merged.map((m, index) => ({
            key: `${contribution.key}-source-${index}`,
            label: `${m.label} ${fromMinor(m.minor)}${m.at}`,
            tone: m.kind ? PILL_TONE[m.kind] : 'neutral',
          }));
          // The engine's own sentences. `spo_number` and `arrival_date` are always null
          // because the SPO and its date are INSIDE the sentence (deviation 2), so the
          // sentence is the only place the fact exists and it may never be dropped - it moves
          // BEHIND the info icon rather than out of the row.
          const why = contribution.sources
            .map((source) => source.reason)
            .join(' ');
          const share = shareNote(contribution);
          const unit = unitNote(contribution);
          const inquiryDecision = contributionInquiryDecision(contribution);
          return (
            <div className="min-w-0">
              <div className="flex items-center gap-1">
                {pills.length === 0 ? (
                  // "Cannot be sourced" is about a line no ladder could answer. A line the
                  // BOOK already decided - a live order inquiry row, no board composition
                  // (14 Sep 2026 ruling) - was never walked, so the slot names the inquiry
                  // purchasing holds instead of declaring the line unsourceable.
                  inquiryDecision ? (
                    <span
                      className="min-w-0 truncate text-sm tabular-nums"
                      title={inquiryDecision.inquiry_no ?? ''}
                    >
                      {inquiryDecision.inquiry_no ?? 'Unnumbered inquiry'}
                    </span>
                  ) : (
                    <span className="text-sm text-muted-foreground">
                      Cannot be sourced
                    </span>
                  )
                ) : (
                  <PillOverflow
                    items={pills}
                    ariaLabel="Sourced from"
                    testId={`sources-pills-${contribution.key}`}
                    className="min-w-0 flex-1"
                    renderPopover={() => (
                      <div className="space-y-2">
                        {merged.map((m, index) => {
                          const option = optionForRung(
                            m.representative.rung,
                            contribution.options ?? [],
                          );
                          return (
                            <div
                              key={index}
                              data-testid={`source-popover-row-${contribution.key}-${index}`}
                              className="space-y-0.5 border-b border-border pb-2 last:border-0 last:pb-0"
                            >
                              <div className="flex items-center justify-between gap-2 font-medium">
                                <span>{m.label}</span>
                                <span className="tabular-nums">
                                  {fromMinor(m.minor)}
                                </span>
                              </div>
                              {m.at.trim() && (
                                <p className="text-muted-foreground">
                                  {m.at.trim()}
                                </p>
                              )}
                              {option && (
                                <p className="text-muted-foreground">
                                  {option.label}
                                </p>
                              )}
                            </div>
                          );
                        })}
                      </div>
                    )}
                  />
                )}
                {/* S3 (`PLAN-local-supplier-oi-routing.md`): mirrors the Suggested/Decided
                    pill the list view carries - a local Buy raises no Order Inquiry. */}
                {contribution.buy_origin === 'local' &&
                  merged.some((m) => m.kind === 'buy') && (
                    <Badge variant="secondary">Local</Badge>
                  )}
                {/* AC-S5-1 (`PLAN-board-received-stock-own-arrival.md`): a Reserve born
                    from goods that landed FOR this line carries `source: 'own_arrival'` -
                    never derived from `rung === 'group_take'` alone, which also covers an
                    ordinary group-take Reserve nothing landed for. */}
                {(() => {
                  const ownArrival = contribution.sources.filter(
                    (source) => source.source === 'own_arrival',
                  );
                  if (ownArrival.length === 0) return null;
                  const minor = ownArrival.reduce(
                    (total, source) => total + toMinor(source.qty),
                    0,
                  );
                  const title = ownArrival
                    .map((source) => source.supply_document)
                    .filter((document): document is string => Boolean(document))
                    .join(', ');
                  return (
                    <Badge
                      variant="secondary"
                      data-testid={`received-own-arrival-${contribution.key}`}
                      title={title || undefined}
                    >
                      Received {fromMinor(minor)}
                    </Badge>
                  );
                })()}
                {/* The two prose sentences behind the numbers above - why this rung fired,
                    and what was left for this line at its own pile - under one visible icon
                    rather than a silent `title` nobody hovers or two lines of wrapped text
                    (the captain: "don't explain too much", "put it under the tooltip"). The
                    numbers stay in the row; only the prose moves. */}
                {(why || share || unit) && (
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <Button
                        type="button"
                        mode="icon"
                        variant="ghost"
                        size="sm"
                        aria-label="Why this composition"
                        data-testid={`source-info-${row.original.key}`}
                        className="size-5 shrink-0 text-muted-foreground"
                      >
                        <Info className="size-3.5" aria-hidden />
                      </Button>
                    </TooltipTrigger>
                    <TooltipContent
                      data-testid={`source-note-${row.original.key}`}
                      className="max-w-xs space-y-1 break-words"
                    >
                      {why && <p>{why}</p>}
                      {share && <p>{share}</p>}
                      {unit && <p>{unit}</p>}
                    </TooltipContent>
                  </Tooltip>
                )}
                {/* The whole ladder behind that strip, structured, under its own icon (the
                    captain: "need more justification ... STRUCTURED instead of plain text
                    explaining, you can put it under the tooltip"). */}
                <BoardTrailPopover contribution={row.original} />
              </div>
            </div>
          );
        },
        size: 150,
        minSize: 130,
        meta: { headerTitle: 'Sourced from' },
      },
      {
        id: 'order_inquiry',
        accessorFn: (row) => row.order_inquiry?.inquiry_no ?? '',
        header: ({ column }) => (
          <DataGridColumnHeader title="Order inquiry" column={column} />
        ),
        // Beside the Decision, because it is the OTHER half of the same answer: the decision
        // is what was promised, the inquiry is what purchasing was actually told to do about
        // it and how far they have got. A dash means nobody has been told anything yet.
        cell: ({ row }) => {
          const inquiry = row.original.order_inquiry;
          if (!inquiry) return <span className="text-muted-foreground">-</span>;
          return (
            <div className="flex min-w-0 items-center gap-1.5">
              <span
                className="min-w-0 truncate tabular-nums"
                title={inquiry.inquiry_no ?? ''}
              >
                {inquiry.inquiry_no ?? '-'}
              </span>
              {/* PR 1622 (owner, mock v3): an open purchasing refusal is this inquiry's
                  status, with who/when/why behind the info icon. */}
              {inquiry.ack_state === 'rejected' ? (
                <OrderInquiryRejectedBadge
                  refusal={inquiry}
                  testId={`breakdown-oi-rejected-${row.original.key}`}
                />
              ) : (
                <OrderInquiryStatePill state={inquiry.state} />
              )}
            </div>
          );
        },
        size: 190,
        minSize: 90,
        meta: { headerTitle: 'Order inquiry' },
      },
      {
        id: 'decision',
        header: ({ column }) => (
          <DataGridColumnHeader title="Decision" column={column} />
        ),
        // A PILL, AND the row's own actions beside it (`BoardVerdictActions`, AC-B11) - the
        // identical set the list view's Verdict column offers, so the two readings of the
        // board teach one gesture. The composition itself is still taken in the expanded
        // row, where the numbers it is made against are; these are the answers that need no
        // numbers: take the proposal, refuse it with a reason, undo, or go and edit it.
        cell: ({ row }) => {
          const key = row.original.key;
          const contribution = row.original;
          return (
            <div className="flex min-w-0 items-center gap-1">
              <BoardDecisionPill contribution={contribution} decision={draft[key] ?? null} />
              {/* AC-DT-5 (`PLAN-oi-decision-trail-ui.md`, round 2): the same History icon
                  the list view's own Verdict column carries, right after the chip -
                  same gate, including `covered` (N1, round 3: a fully-reserved /
                  local-buy covered line can carry no `decision` object and no OI row of
                  its own and still be worth tracing). */}
              {contribution.decision ||
              contribution.draft ||
              contribution.order_inquiry ||
              contribution.covered ? (
                <DecisionTrailButton
                  coreLineId={contribution.line_id ?? null}
                  itemCode={contribution.item_code}
                  className="shrink-0"
                />
              ) : null}
              <BoardVerdictActions
                contribution={contribution}
                decision={draft[key] ?? null}
                onDecide={(next) => onDecide(key, next)}
                onChange={() => openRow(key)}
              />
            </div>
          );
        },
        // AC-C2: the pill and its icons, on one line at 375px - the column was 140 for a
        // pill and at most one icon. Resizable like every other column here.
        size: 200,
        minSize: 170,
        enableSorting: false,
        meta: { headerTitle: 'Decision' },
      },
    ],
    [
      cell.contributions,
      cell.locations,
      draft,
      multiProduct,
      onDecide,
      openRow,
      setDirty,
    ],
  );

  return (
    /* THE X, ESCAPE AND THE BACKDROP ARE ALSO A DISCARD (C5). An expanded panel holding a
       half-composed decision is thrown away by any of them, and the row-switch prompt guarded
       only the fourth gesture, so the three easiest ways out of the dialog lost the draft in
       silence. `requestClose` asks first and closes only once the question is answered. */
    <Dialog open onOpenChange={(next) => !next && requestClose(onClose)}>
      {/* THE SCM FAMILY'S SHELL, copied from `scm/components/PlanRowDialog.tsx` the way
          `project-sales/stock-debt/components/StockDebtCellDialog.tsx` copied it: same sizing,
          same header, same scrolling body, so every lightbox in this family is one object to a
          reader. At whichever merge lands last, the three re-point at one file. */}
      <DialogContent
        data-testid="cell-dialog-content"
        className="flex max-h-[85vh] w-full flex-col overflow-hidden p-0 sm:max-w-[95vw]"
      >
        {/* SLIM, AND IT DOES NOT SCROLL. It used to be capped at 45vh and hold three cards and
            a stock table of up to eleven location rows - which at a 900px window is the whole
            dialog, leaving the body about 100px and the decision panel inside it unreachable
            without scrolling a region a reader could not see. The header states what the cell
            IS; everything a planner reads is in the body, in one region, under tabs.

            `pe-10` is the one departure from the copied shell: the close button is absolute at
            `end-5`, and at 375px the date and the flag chips ran underneath it. */}
        <DialogHeader className="shrink-0 space-y-1 border-b p-4 pe-10 sm:p-6 sm:pe-10">
          <div className="flex flex-wrap items-center gap-2">
            <DialogTitle className="min-w-0 break-words">
              {`${cell.item_code} · ${bucketLabel}`}
            </DialogTitle>
            {/* Dealer / project hot-selling, beside the title and not only inside the trail
                popover: it is the fact that decides whether the pool is offered at all, and the
                captain (28 Aug 2026) wanted it read before the suggestion, not dug for. The
                flags are per ITEM, so any line the ladder walked states the cell's. */}
            {flaggedContribution ? (
              <ItemFlagChips contribution={flaggedContribution} idKey="cell" />
            ) : null}
          </div>
          {/* The muted context line the family's shell carries. WHOSE PILE THIS IS, which is
              the one fact about the cell that neither the title nor either tab states: the
              ownership group is what step 1 draws from, and the stock table below only names
              it when there is none. It falls back to the quantity the dialog was opened over,
              so the accessible description is never empty. */}
          <DialogDescription
            data-testid="cell-dialog-context"
            className="truncate text-xs"
            title={context}
          >
            {context}
          </DialogDescription>
        </DialogHeader>

        {/* THE ONLY SCROLLING REGION, exactly as the family's shell has it, and it holds the
            row actions. Nothing here can be painted over: there is no footer. */}
        <DialogBody
          data-testid="cell-dialog-body"
          className="min-h-0 flex-1 space-y-3 overflow-y-auto p-4 sm:p-6"
        >
          {/* The decision, first and in two small cards: what is being asked for, and what
              the ladder proposes to do about it. The dialog used to open on a sentence and a
              table of lines, and the planner had to read a source strip per row to work out
              what the whole cell was being asked to decide - which is the one thing they came
              for. "across 1 line" is gone with it: the Contributing lines tab IS the lines. */}
          <div
            className={cn(
              'grid grid-cols-1 gap-3',
              decision.length > 0 ? 'sm:grid-cols-3' : 'sm:grid-cols-2',
            )}
          >
            <StatCard
              testId="cell-quantity-needed"
              label="Quantity needed"
              value={cell.total_qty}
              sub={`${decided} decided`}
            />

            {fulfilledLines.length > 0 ? (
              <div
                data-testid="cell-fulfilled"
                className="rounded-md border border-border p-3 text-sm"
              >
                <div className="text-xs font-medium text-muted-foreground">Fulfilled</div>
                {fulfilledLines.map((entry) => (
                  <div key={entry.key} className="mt-1">
                    {`${entry.qty_delivered ?? '0'} delivered`}
                    {entry.required_date
                      ? `, due ${formatDateInMalaysia(entry.required_date)}`
                      : ''}
                  </div>
                ))}
              </div>
            ) : (
              <CompositionCard
                testId="cell-suggestion"
                rowTestId="suggestion"
                title={
                  suggestionIsStale
                    ? `Suggestion (before ladder ${LADDER_VERSION})`
                    : 'Suggestion'
                }
                rows={suggestion}
                empty={
                  suggestionRecorded
                    ? 'Nothing proposed for this cell'
                    : 'Not recorded for this revision'
                }
              />
            )}

            {/* Beside the suggestion, never instead of it (AC-D3). SAME component, two
                inputs: two cards that merely resembled each other would drift, and the whole
                point is that a planner compares them at a glance. Rendered only once
                something IS decided. */}
            {decision.length > 0 ? (
              <CompositionCard
                testId="cell-decision"
                rowTestId="decision"
                title="Decision"
                rows={decision}
                empty=""
                moves={moves}
              />
            ) : null}
          </div>

          {/* NO RANKING SENTENCE. It explained the Rank column, and the Rank column is gone
              (R8), so it was a paragraph about a number nobody can see. */}

          {/* ONE SECTION, TWO TABS, the way `StockDebtCellDialog` puts its two grids. The two
              tables used to be stacked - the stock position in the header, the lines under it -
              and each got about half the dialog, so a reader scrolled past one to reach the
              other and neither was ever whole. A tab gives whichever table is being read the
              WHOLE body, and the count sits in the trigger where it can be read without
              pressing anything.

              STOCK LEADS, because the dialog is opened from a CELL and never from a line
              (`FulfilmentBoardPanel` passes `onOpenCell(cell)` and nothing else): there is no
              line context to default to the lines with. Opening a row in Contributing lines
              re-points the stock table at that line, which is the crossing between them. */}
          <Tabs defaultValue="stock">
            <TabsList>
              <TabsTrigger value="stock">Stock</TabsTrigger>
              <TabsTrigger value="lines">
                {`Contributing lines (${cell.contributions.length})`}
              </TabsTrigger>
            </TabsList>

            <TabsContent value="stock">
              {/* The suggestion, as a SENTENCE rather than only as the composition card above
                  (mockup round 4): the donor SO and the SPO document a Borrow names are links
                  here, and nowhere else carries the words to make them one. Only sources that
                  NAME a donor or a document print one - a Reserve or a Buy has neither and
                  says nothing extra. */}
              {(donorSources.length > 0 || documentSources.length > 0) && (
                <div
                  data-testid="cell-suggestion-sentence"
                  className="mb-3 space-y-1"
                >
                  {(shownContribution?.sources ?? [])
                    .filter(
                      (source) =>
                        source.donor_so_number || source.supply_document,
                    )
                    .map((source, index) => {
                      // Each link jumps by THIS source's own donor/document, not by the
                      // dialog's single "first of the cell" match (review round, S3) - a
                      // step-2 combine can name several donors on one line (R35), and a
                      // second donor's own link has to land its own row.
                      const sourceDonor: StockDonorMatch | undefined =
                        source.donor_so_number
                          ? {
                              soNumber: source.donor_so_number,
                              lineId: source.donor_core_line_id,
                              location: source.location,
                            }
                          : undefined;
                      const sourceDocument: StockDocumentMatch | undefined =
                        source.supply_document &&
                        isJumpableDocument(source.supply_document)
                          ? {
                              spoNumber: source.supply_document,
                              location: source.location,
                            }
                          : undefined;
                      // The captain's own complaint (3 Sep 2026, SO418869
                      // SRTWCX7405-RL-S-PJ): the chip only ever jumped the table below, and
                      // there was no way OUT of the dialog onto the document itself.
                      const documentHref = sourceDocument
                        ? spoDetailHref(
                            spoNumberFromLabel(source.supply_document as string),
                          )
                        : undefined;
                      return (
                        <p
                          key={`${source.kind}-${index}`}
                          className="text-sm text-muted-foreground"
                        >
                          {annotateReason(
                            source,
                            {
                              onDonorClick: () =>
                                stockTableRef.current?.jumpToDonor(sourceDonor),
                              onDocumentClick: () =>
                                stockTableRef.current?.jumpToDocument(
                                  sourceDocument,
                                ),
                            },
                            documentHref,
                          )}
                        </p>
                      );
                    })}
                </div>
              )}

              {/* The sticky toolbar (AC-3.2/3.9): search plus the three jump buttons, pinned
                  to the top of the DIALOG'S OWN scroll (`DialogBody`, `overflow-y-auto`) so
                  they stay reachable while the table below scrolls underneath them. Donor and
                  document buttons render only when the shown line's suggestion names one. */}
              <div className="sticky top-0 z-10 mb-3 flex flex-wrap items-center gap-2 border-b border-border bg-background py-2">
                <ListSearchInput
                  value={stockSearch.value}
                  onChange={stockSearch.setValue}
                  isSettling={stockSearch.isSettling}
                  placeholder="Search SO number, customer, agent"
                  className="w-full sm:w-72"
                />
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  data-testid="stock-jump-this-line"
                  onClick={() => stockTableRef.current?.jumpToThisLine()}
                >
                  My line
                </Button>
                {donorMatches.length > 0 ? (
                  <Button
                    type="button"
                    size="sm"
                    variant="outline"
                    data-testid="stock-jump-donor"
                    className="border-amber-300 text-amber-700 dark:border-amber-800 dark:text-amber-400"
                    onClick={() => stockTableRef.current?.jumpToDonor()}
                  >
                    Donor
                  </Button>
                ) : null}
                {documentMatch ? (
                  <Button
                    type="button"
                    size="sm"
                    variant="outline"
                    data-testid="stock-jump-document"
                    className="border-emerald-300 text-emerald-700 dark:border-emerald-800 dark:text-emerald-400"
                    onClick={() => stockTableRef.current?.jumpToDocument()}
                  >
                    {documentMatch.spoNumber}
                  </Button>
                ) : null}
              </div>

              {/* What is actually AT each location, not only what is outstanding from it - the
                  captain's "where will I need to source to fulfil", answered with facts, and the
                  dialog has to carry it because a reader who opened it from a cell they can no
                  longer see still needs to know one cell can draw on several locations.

                  KEYED BY THE CELL, so the locations a reader expanded close when the dialog is
                  pointed at a different cell: an expansion left open would otherwise show the
                  previous cell's documents under the new cell's row. */}
              <CellStockTable
                key={cellKey}
                ref={stockTableRef}
                locations={shownLocations}
                groupNote={cell.location_group_note}
                taken={taken}
                takenKinds={takenKinds}
                lineIds={askingLineIds}
                forLine={
                  cell.contributions.length > 1 && shownContribution
                    ? `${shownContribution.so_number} ${soLineLabel(shownContribution).toLowerCase()}`
                    : undefined
                }
                donor={donorMatches}
                documentInfo={documentMatch}
                filterText={stockSearch.debouncedValue}
                landOnMount
                poolSharePct={poolSharePct}
              />
            </TabsContent>

            <TabsContent value="lines">
              <PanelDataGrid<BoardContribution>
                title="Contributing lines"
                columns={columns}
                // AC-3.5: the same search box, filtering THESE rows by SO number, customer or
                // agent - never a second fetch, `cell.contributions` is already the whole cell.
                rows={filteredContributions}
                getRowId={(row) => row.key}
                pageResetKey={linesSearch.debouncedValue}
                listingKey="projects.projects.view::project-board-cell-breakdown"
                sortable
                expanded={expanded}
                onExpandedChange={setExpanded}
                // The whole row opens its decision panel; the chevron in the first cell is only
                // the indicator that says which way it will go.
                onRowClick={(row) => requestRow(row.key)}
                rowSelection={rowSelection}
                onRowSelectionChange={setRowSelection}
                // A covered row is not selectable either: the bulk verbs are Approve and Reject,
                // and a bulk Reject sweeping up a confirmed line would silently un-decide it,
                // which is the very defect the covered state exists to stop. Nor is a row that
                // already carries a draft - a bulk verb would overwrite an amended composition
                // with the engine's own, which is the list view's rule too.
                enableRowSelection={(row) => canQuickSave(row.original, draft)}
                toolbar={
                  <div className="flex flex-wrap items-center gap-2">
                    <ListSearchInput
                      value={linesSearch.value}
                      onChange={linesSearch.setValue}
                      isSettling={isSearchInFlight(
                        linesSearch.isSettling,
                        false,
                        linesSearch.debouncedValue,
                      )}
                      placeholder="Search SO number, customer, agent"
                      className="w-full sm:w-72"
                    />
                    {/* D14: the cell's own "nothing here needs a second look" button - every
                    still-outstanding, not-yet-drafted line in the cell, with no selection to
                    make first. Reversible with the per-line Undo below, so no confirmation. */}
                    <Button
                      type="button"
                      size="sm"
                      variant="outline"
                      disabled={suggestibleContributions.length === 0}
                      onClick={saveAllSuggested}
                    >
                      Save all suggested
                    </Button>
                    {selectedKeys.length > 0 ? (
                      <>
                        {/* Says exactly how many rows the verbs will act on. With a paginated cell
                        the header ticks this page, and this count is what was ticked - so the
                        strip never implies more than it will do. */}
                        <Badge
                          variant="secondary"
                          className="h-8 gap-1 px-2.5 text-sm"
                        >
                          {`${selectedKeys.length} selected`}
                        </Badge>
                        <Button
                          type="button"
                          size="sm"
                          onClick={approveSelected}
                        >
                          <Check className="size-4" aria-hidden />
                          Approve selected
                        </Button>
                        <Button
                          type="button"
                          size="sm"
                          variant="outline"
                          onClick={() =>
                            decideSelected({
                              verdict: 'rejected',
                              reason: 'Rejected on the planning board.',
                            })
                          }
                        >
                          <X className="size-4" aria-hidden />
                          Reject selected
                        </Button>
                        <Button
                          type="button"
                          size="sm"
                          variant="ghost"
                          onClick={() => setRowSelection({})}
                        >
                          Clear
                        </Button>
                      </>
                    ) : null}
                  </div>
                }
                emptyTitle={
                  linesSearch.debouncedValue
                    ? 'No line matches your search'
                    : 'No line contributes to this cell'
                }
                emptyBody="Nothing in the selection is outstanding for this product by this date."
                pageSize={25}
                // The DialogBody above already owns the scroll viewport (overflow-y-auto).
                scrollerMaxHeight={false}
              />
            </TabsContent>
          </Tabs>
        </DialogBody>

        <UnsavedDecisionPrompt state={expansion} />
      </DialogContent>
    </Dialog>
  );
}

/**
 * ONE composition, as the dialog's header states it: a badge per kind and the quantity per
 * location beside it (AC-D3).
 *
 * One component for BOTH the suggestion and the decision, because the two are read against
 * each other: a second card that merely looked like this one would drift the first time
 * either changed, and the comparison is the whole reason they sit side by side.
 *
 * Only the kinds with a quantity, in one fixed order, so Buy sits above the stock rows
 * wherever it appears. The empty kinds used to be listed and muted, and on a real cell that
 * was three lines of nothing around the one line that said what to do.
 */
function CompositionCard({
  testId,
  rowTestId,
  title,
  rows,
  empty,
  moves,
}: {
  testId: string;
  /** Prefix for the per-kind rows: `suggestion-buy`, `decision-shared`. */
  rowTestId: string;
  title: string;
  rows: SuggestionRow[];
  empty: string;
  /**
   * The movements this composition implies ("454 DC1-BB -> BRW-BB"), on the Decision card
   * only. Empty when nothing has to move, and the line is not drawn then: a Moves row
   * reading nothing claims a decision was taken to carry nothing.
   */
  moves?: string;
}) {
  return (
    <div data-testid={testId} className="rounded-lg border border-border p-3">
      <p className="mb-1.5 text-xs text-muted-foreground">{title}</p>
      <div className="space-y-1">
        {rows.length === 0 && empty ? (
          <p className="text-sm text-muted-foreground">{empty}</p>
        ) : null}
        {rows.map((row) => (
          <div
            key={row.key}
            data-testid={`${rowTestId}-${row.key}`}
            className="flex flex-wrap items-center gap-1.5 text-sm"
          >
            <Badge variant="primary" appearance="light" size="sm">
              {row.label}
            </Badge>
            {/* The quantity PER LOCATION ("454 from DC1-BB, 267 from MWH-BB"), not a total
                beside a bare list of codes: the split IS the instruction, and somebody has
                to key each movement of it. */}
            <span className="min-w-0 break-words tabular-nums">
              {rowText(row)}
            </span>
            {/* The rule's own sentence, and only when every source on the row gives the same
                one: a Buy for "nothing free anywhere" and a Buy for "beyond the lead time
                window" are the same number for opposite reasons, and this card is where that
                is decided. */}
            {row.note ? (
              <span className="w-full text-xs text-muted-foreground">
                {row.note}
              </span>
            ) : null}
          </div>
        ))}
      </div>
      {moves ? (
        <p
          data-testid={`${rowTestId}-moves`}
          className="mt-2 border-t border-border pt-1.5 text-xs"
        >
          <span className="text-muted-foreground">Moves: </span>
          <span className="break-words tabular-nums">{moves}</span>
        </p>
      ) : null}
    </div>
  );
}

/**
 * A figure the server actually stated, as opposed to one it left out.
 *
 * A NULL is "not stated", never 0. The two are opposite instructions - 0 means do not look
 * here, nothing stated means nobody has said where to look.
 */
function present(value: string | null | undefined): boolean {
  return value !== null && value !== undefined;
}

/**
 * Why this line's Reserve is the size it is: who was ahead of it at its OWN location, and what
 * was left when it was reached (PLAN 13.7, the fair-share amendment).
 *
 * The captain's card, live: pile Available -8013 at BRW-BB, and yet a Reserve of 80 stands -
 * because 6 lines ahead wanted 388 of the 1015 on hand and 627 remained for this line. The strip
 * above states the pile; this states the line. THEY ARE DIFFERENT NUMBERS AND NEITHER IS EVER
 * PRINTED UNDER THE OTHER'S LABEL: "Available" is the whole pile, "left for this line" is this
 * line's share at its own location.
 *
 * It says what remained and claims NOTHING about what may be reserved. `qty_proposed_reserve` can
 * exceed this figure, because the shared pool is a second source with its own queue - a live line
 * reading "0 left for this line" still reserved 9 from the pool, and the source strip beside it
 * already says "Reserve 9 at BRW".
 *
 * Absent is absent: a line the server sent no share for, and a line whose sales order states no
 * location (it has no pile to be queued at), get no sentence rather than a 0.
 */
/**
 * Ladder v6: the line was not planned alone. The captain, 28 August 2026, on SO381895 lines
 * 31 and 32 (10 borrowed, 20 bought): "this is 1 order as a whole ... for the same delivery
 * date". Said only when there IS another line in the unit; the ordinary line says nothing.
 *
 * S2 (R-E) walks the unit's lines ONE AT A TIME, smallest quantity first, each sharing the
 * piles the one before it left - so two lines of one unit can (and on SO419208's 135-then-
 * 1305 shape, do) end with DIFFERENT compositions. The row above already prints THIS line's
 * own `sources`; this sentence used to say "covered or bought as one" and that read as
 * "identically", which R-E ended. It now says only what is still true: the UNIT's total, not
 * how the lines inside it got there - that is what the Sourced-from cell of every other line
 * of this unit is for.
 */
function unitNote(contribution: BoardContribution): string | null {
  const count = contribution.unit_line_count ?? 1;
  if (count <= 1 || !present(contribution.unit_qty)) return null;
  const others = count - 1;
  const when = contribution.required_date
    ? ` for ${formatDateInMalaysia(contribution.required_date)}`
    : '';
  return `Planned with ${others} other ${others === 1 ? 'line' : 'lines'} of this order${when}: ${contribution.unit_qty} in all. Each line's own composition is shown on its own row.`;
}

function shareNote(contribution: BoardContribution): string | null {
  if (contribution.unplannable) return null;
  if (!present(contribution.available_to_this_line)) return null;
  const left = contribution.available_to_this_line;
  const at = contribution.fulfilment_location;
  const lines = contribution.lines_ahead ?? 0;
  if (lines === 0) {
    return `First in the queue${at ? ` at ${at}` : ''} · ${left} left for this line`;
  }
  const wanted = present(contribution.so_qty_ahead)
    ? contribution.so_qty_ahead
    : '0';
  return `${lines} line${lines === 1 ? '' : 's'} ahead wanting ${wanted} · ${left} left for this line${
    at ? ` at ${at}` : ''
  }`;
}

/**
 * Exported so another surface reading the same sources (`PLAN-so-book-diff-replanning.md`) can print a
 * replan/qty_up row's proposal in the same words the board's own breakdown does, rather than a
 * second sentence-builder that drifts from this one.
 *
 * SECTION 2'S WORDS, off `SHORT_LABELS`. This strip used to print ladder v2's rung names
 * (Pool / Group take / Group borrow / Cross-group borrow) inside a popover whose Suggestion
 * card, colour bar and legend all said Shared / Own / Borrow for the same quantities. One
 * composition may not be described twice: the plan's table is the only vocabulary.
 *
 * `ownLocation` is the line's own warehouse code, which is what tells the agent's own group
 * from the shared pool for a source that carries no rung.
 */
export function sourceLabel(
  source: BoardContribution['sources'][number],
  ownLocation?: string | null,
): string {
  // The WHOLE source, never a kind and a rung pulled out of it: the location is what tells
  // the agent's own group from the shared pool when the source carries no rung, and passing
  // the pieces by hand is how it came to be left out.
  const row = rowOf(source, ownLocation);
  return row ? SHORT_LABELS[row] : 'Cannot be sourced';
}

/**
 * Where the quantity comes from, in the preposition each kind takes.
 *
 * A Reserve is held AT a location; a Borrow comes FROM somebody else's. "Borrow 10 at MWH-IB"
 * reads as stock this line has there, which is the opposite of what a borrow is. A borrow
 * FROM AN ORDER names its donor SO line instead, when one was stated - "from SO371334 line 2"
 * is the identity that matters, the location is secondary.
 *
 * ANY borrow that names a donor, not only the retired `group_borrow` rung: ladder v7.1's
 * step 2 (`order_borrow`), step 3 (`supply_borrow`) and the pool's borrow half all take a
 * later ORDER's quantity, and gating on the one v2 rung printed the LOCATION for every one
 * of them - so the row said "from MWH-IB" where the whole point is whose order it was.
 */
/** Exported for the same reason `sourceLabel` is - see its comment. */
export function sourceAt(source: BoardContribution['sources'][number]): string {
  // LADDER v7.1 STEP 3 (S4, AC-S4-5): a DOCUMENT, so the row names the document and the day
  // it lands - "SPO 202607-S0105, arriving 15 Sep 2026" - before it names whose order gives
  // it up. Both facts come from the server as fields (the client never parses them back out
  // of the sentence), and the arrival is the whole reason a planner would accept this row
  // over a Buy.
  if (source.supply_document) {
    const arriving = source.arrival_date
      ? `, arriving ${formatDateInMalaysia(source.arrival_date)}`
      : '';
    const from = source.donor_so_number
      ? ` from ${source.donor_so_number}${
          source.donor_line_no !== null && source.donor_line_no !== undefined
            ? ` line ${source.donor_line_no}`
            : ''
        }`
      : '';
    return ` ${source.supply_document}${arriving}${from}`;
  }
  if (source.kind === 'borrow' && source.donor_so_number) {
    const line =
      source.donor_line_no !== null && source.donor_line_no !== undefined
        ? ` line ${source.donor_line_no}`
        : '';
    return ` from ${source.donor_so_number}${line}`;
  }
  if (!source.location) return '';
  return source.kind === 'borrow'
    ? ` from ${source.location}`
    : ` at ${source.location}`;
}

/**
 * The option row a source's pill "came from", for the Sourced-from popover (S3b, R-J: "the
 * option row it came from").
 *
 * `source.rung` is the ENGINE'S OWN RUNG CONSTANT (`rowOf`'s vocabulary: `group_take`,
 * `order_borrow`, `pool`, ...) and `options[].step` is the FIVE-ROW LADDER v8 vocabulary
 * (`BoardLadderStep`); the two predate each other and do not share every spelling, so this is
 * a best-effort match rather than a lookup - `group_take` reads as the `use` step (both
 * halves of it), a `pool` rung reads as `pool_share` (falling back to the legacy `pool` step a
 * frozen v7.1 trail may still carry), and everything else already spells the same word in
 * both vocabularies. A rung with no matching step (the retired `cross_group_borrow` /
 * `group_borrow`, frozen-snapshot only) returns nothing rather than a wrong guess.
 */
function optionForRung(
  rung: string | null | undefined,
  options: BoardLadderOption[],
): BoardLadderOption | undefined {
  if (!rung) return undefined;
  const step =
    rung === 'group_take'
      ? 'use'
      : rung === 'pool'
        ? 'pool_share'
        : rung;
  const found = options.find((option) => option.step === step);
  if (found) return found;
  if (step === 'pool_share') return options.find((option) => option.step === 'pool');
  return undefined;
}

/**
 * Whether a `supply_document` value could ever BE a row `StockDocumentsPanel` shows (review
 * round, S3). An SPO is one; a historical PO snapshot (`PO 202607-P0031 line 3`, the ladder
 * v7.1 step-3 spelling for a project PO) is not - a PO reaches a project line through its own
 * link, never by sitting in the stock ledger the drill reads, so a jump built from one would
 * fall through to the own-location section with nothing to show for it. The prefix is the
 * server's own spelling (`fulfilmentPlanningService.ts`'s module doc), read rather than
 * re-derived.
 */
function isJumpableDocument(value: string | null | undefined): boolean {
  return Boolean(value) && !(value as string).trim().toUpperCase().startsWith('PO ');
}

/**
 * The engine's own sentence (`source.reason`), with the donor SO and/or the SPO document it
 * NAMES turned into jump links in place (AC-3.3/3.4/3.13) - never a second, client-built
 * sentence beside the server's, which is how a Reserve's wording and a Borrow's would start
 * to read as two different vocabularies.
 *
 * Splits on the EXACT substrings the server already sent (`donor_so_number`,
 * `supply_document`) rather than re-parsing the prose: the two markers are known values, so
 * a plain search-and-wrap is the whole job, and a regex over free text would be guessing at
 * a grammar the server never promised to keep stable.
 *
 * `documentHref`, when the source names a document, adds a SECOND, tiny control right after
 * the chip: an external-link icon that opens the SPO itself. The chip keeps its own job -
 * jumping the table below to the matching row (AC-3.4) - because the captain's own screenshot
 * (3 Sep 2026, SO418869 SRTWCX7405-RL-S-PJ) shows that working; there was simply no way OUT
 * of the dialog onto the document, so this adds one rather than repurposing the other.
 */
function annotateReason(
  source: BoardSource,
  handlers: { onDonorClick: () => void; onDocumentClick: () => void },
  documentHref?: string,
): React.ReactNode {
  const markers = [
    source.donor_so_number
      ? {
          value: source.donor_so_number,
          testId: 'suggestion-donor-link',
          onClick: handlers.onDonorClick,
        }
      : null,
    source.supply_document
      ? {
          value: source.supply_document,
          testId: 'suggestion-document-link',
          onClick: handlers.onDocumentClick,
        }
      : null,
  ]
    .filter(
      (
        marker,
      ): marker is { value: string; testId: string; onClick: () => void } =>
        Boolean(marker),
    )
    .map((marker) => ({
      ...marker,
      index: source.reason.indexOf(marker.value),
    }))
    .filter((marker) => marker.index >= 0)
    .sort((a, b) => a.index - b.index);

  if (markers.length === 0) return source.reason;

  const nodes: React.ReactNode[] = [];
  let cursor = 0;
  markers.forEach((marker, position) => {
    if (marker.index < cursor) return; // overlapping matches: keep the first, skip the rest
    nodes.push(source.reason.slice(cursor, marker.index));
    nodes.push(
      <button
        key={`${marker.testId}-${position}`}
        type="button"
        data-testid={marker.testId}
        onClick={marker.onClick}
        className="font-medium text-primary underline-offset-2 hover:underline"
      >
        {marker.value}
      </button>,
    );
    if (marker.testId === 'suggestion-document-link' && documentHref) {
      nodes.push(
        <Link
          key={`suggestion-document-open-${position}`}
          href={documentHref}
          onClick={(event) => event.stopPropagation()}
          className="ms-1 inline-flex align-middle text-muted-foreground hover:text-primary"
          aria-label={`Open ${marker.value}`}
          data-testid="suggestion-document-open-link"
        >
          <ExternalLink className="size-3.5" aria-hidden />
        </Link>,
      );
    }
    cursor = marker.index + marker.value.length;
  });
  nodes.push(source.reason.slice(cursor));
  return nodes;
}

/** The outstanding quantity: the server's own name for it when it sends one. */
function outstandingOf(contribution: BoardContribution): string {
  return contribution.qty_outstanding ?? contribution.qty;
}

function sumOf(
  contributions: BoardContribution[],
  pick: (contribution: BoardContribution) => string,
): string {
  return fromMinor(
    contributions.reduce(
      (total, contribution) => total + toMinor(pick(contribution)),
      0,
    ),
  );
}

// The evidence behind the score used to be a `title` sentence built here. It is a table now
// (`BoardRankPopover`): the captain asked to see the CALCULATION, and one row per factor with
// its fact, score, weight and product - plus the division that produces the number - is the
// shape a person can check. Two renderings of one arithmetic is how they drift.
