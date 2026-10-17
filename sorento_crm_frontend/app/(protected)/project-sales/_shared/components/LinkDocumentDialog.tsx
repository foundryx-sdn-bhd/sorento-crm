'use client';

import * as React from 'react';
import {
  type ColumnDef,
  type ExpandedState,
  getCoreRowModel,
  getExpandedRowModel,
  useReactTable,
} from '@tanstack/react-table';
import { ChevronDown, ChevronRight, PackageSearch } from 'lucide-react';
import {
  Alert,
  AlertContent,
  AlertDescription,
  AlertIcon,
  AlertTitle,
} from '@/components/ui/alert';
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { NumberInput } from '@/components/ui/number-input';
import { numberFieldError } from '@/lib/number-field';
import { useStableColumns } from '@/hooks/useStableColumns';
import { Skeleton } from '@/components/ui/skeleton';
import { DataGrid } from '@/components/ui/data-grid';
import { DataGridTable } from '@/components/ui/data-grid-table';
import { DataGridColumnHeader } from '@/components/ui/data-grid-column-header';
import { formatDateInMalaysia } from '@/lib/helpers';
import { cn } from '@/lib/utils';
import {
  useOrderInquiryPlacementMutations,
  useOrderInquiryPoCandidates,
} from '../hooks/useOrderInquiry';
import { formatInquiryQty } from '../lib/orderInquiryWorklist';
import { formatHorizon, isDueAfterHorizon } from '../lib/linkHorizon';
import type { OrderInquiryPoAllocation, OrderInquiryPoCandidate } from '../types/orderInquiry.types';

/** A stable reference for "no candidates yet" - never a fresh `[]` literal at the read
 * site, which would give the grid's `data` prop a new identity every render and spin
 * TanStack's `autoResetPageIndex` loop (measured on the M5 lane, 5 Sep 2026). */
const EMPTY_CANDIDATES: OrderInquiryPoCandidate[] = [];

/**
 * How a candidate is addressed. Exactly one of the two ids is set - the same rule the
 * link row's own CHECK constraint holds - so this is total, never a coalesce that could
 * silently key two different documents the same way.
 */
function candidateKey(candidate: OrderInquiryPoCandidate): string {
  return candidate.kind === 'spo'
    ? `spo:${candidate.spo_allocation_id ?? ''}`
    : `po:${candidate.po_line_id ?? ''}`;
}

/**
 * How well this document line's location fits the row's own (Q5). Named, not numbered:
 * "tier 3" says nothing to a buyer, "Site pool" says which split to key into AutoCount.
 */
const TIER_LABEL: Record<number, string> = {
  1: 'Same location',
  2: 'Same group',
  3: 'Site pool',
  4: 'Sibling location',
  5: 'Elsewhere',
};

function takeError(raw: string): string | undefined {
  return raw.trim() ? numberFieldError(raw, { min: 0 }) : undefined;
}

function toNumber(value: string): number {
  const parsed = Number.parseFloat(value);
  return Number.isFinite(parsed) ? parsed : 0;
}

/** `12.50` -> `MYR 12.50`; blank when the PO line carries no price. Not a guess at what
 *  a supplier would charge - only what the line already holds. */
function formatCandidateUnitPrice(
  unitCost?: string | null,
  currency?: string | null,
): string | null {
  if (unitCost === null || unitCost === undefined || unitCost === '') return null;
  const value = Number.parseFloat(unitCost);
  if (!Number.isFinite(value)) return null;
  return `${currency ?? 'MYR'} ${value.toFixed(2)}`;
}

/**
 * Link PO / Link SPO (PLAN-scm-cs-planning-uat.md section 3.I, AC-I1/AC-I2/AC-I10).
 *
 * Linking happens AUTOMATICALLY on confirm, on PO import and on PO confirm, so this
 * dialog is OVERRIDE + AUDIT rather than the workflow. It opens showing what the cascade
 * would take off each candidate (`default_take`), lets that be adjusted per line -
 * clearing a line to `0` drops it - and posts the whole set in one call. The row keeps
 * its FULL quantity whatever is taken: what is not linked stays demand, and the row reads
 * partly linked.
 *
 * Candidates arrive in the walk's own order and are NEVER filtered by location, only
 * ranked by it (Q5): tier 1 is the row's own location, 2 the same group at another site,
 * 3 a site pool, 4 a sibling at the site. An SPO allocation is a candidate for an ORDER
 * BACK row and for nothing else (captain, 25 Aug), and the document CS cited comes first.
 *
 * The candidate table is the shared `DataGrid`/`DataGridTable` (S8, AC-CF-26): fixed table
 * layout, resizable columns, no pagination - a candidate list is short and a page 2 would
 * hide a line the buyer expects in one scroll, the same carve-out a document's own line
 * table already takes. `listingKey={null}` (never `PanelDataGrid`, which cannot take one):
 * this table is a per-open, ephemeral picker, not a listing a reader's column order is
 * worth remembering across sessions, and skipping the fetch is also what keeps it working
 * in a bare render with no `useListingColumnPreferences` mock. The Document column never
 * truncates and states "line N of M" when the same document contributes several candidate
 * lines - a PO with eight lines used to show the same truncated number eight times with
 * nothing to tell them apart.
 */
export function LinkDocumentDialog({
  rowId,
  itemCode,
  qty,
  linkedQty,
  deliveryDate,
  linkUpTo,
  onDone,
}: {
  rowId: string;
  itemCode?: string | null;
  qty: string;
  /**
   * What the row already has on documents. The dialog is looking for the REMAINDER: a row
   * linked 5 of 8 opens needing 3, and offering it 8 would let a person compose an
   * allocation the backend refuses as over-allocated.
   */
  linkedQty?: string | null;
  /** When this row is due, so the horizon below has something to compare against. */
  deliveryDate?: string | null;
  /**
   * The horizon the page's own presses run to (AC-LH3), `YYYY-MM-DD`. Shown at the top,
   * and a row due past it carries a notice - but the take is still allowed: this dialog is
   * override and audit rather than the workflow, so a person who has looked at the line is
   * never refused it.
   */
  linkUpTo?: string | null;
  onDone: () => void;
}) {
  const candidatesQuery = useOrderInquiryPoCandidates(rowId);
  const { placeAllocations } = useOrderInquiryPlacementMutations();
  // Stable across a render that touches nothing to do with the query itself (typing in a
  // Take input, expanding a row) - see `EMPTY_CANDIDATES`'s own note.
  const candidates = React.useMemo(
    () => candidatesQuery.data?.candidates ?? EMPTY_CANDIDATES,
    [candidatesQuery.data],
  );
  const [expanded, setExpanded] = React.useState<ExpandedState>({});
  /**
   * ONLY the hand edits, keyed by candidate. The cascade's own preview is read straight
   * off the candidate in `takeFor` below rather than copied into state by an effect, so
   * it is in force on the very first render that shows the table: no window in which the
   * dialog offers blank takes and a dead Link button, and no way for a background refetch
   * to clobber an edit (the reason the copy was taken once, before).
   */
  const [edits, setEdits] = React.useState<Record<string, string>>({});

  /**
   * What this candidate is taking: the hand edit when there is one; else this row's own
   * CURRENT link on that line (S8, AC-CF-24) - prefilled so re-pressing Link with nothing
   * touched resubmits the same set; else the cascade's own preview (`default_take`,
   * server-computed, so it can never disagree with what auto-place itself would do). A
   * preview of zero reads blank - nothing is being taken off that line.
   */
  function takeFor(candidate: OrderInquiryPoCandidate): string {
    const edit = edits[candidateKey(candidate)];
    if (edit !== undefined) return edit;
    if (toNumber(candidate.current_take ?? '0') > 0) return candidate.current_take as string;
    return toNumber(candidate.default_take) > 0 ? candidate.default_take : '';
  }

  function setTake(key: string, value: string) {
    setEdits((prev) => ({ ...prev, [key]: value }));
  }

  // AC-CF-26: "line N of M" when the same document contributes several candidate lines -
  // a PO with eight lines otherwise showed the same truncated number eight times with
  // nothing to tell them apart. `M` is a count over the CANDIDATE list, not the document's
  // own line count: only the lines actually offered to this row are what "of M" answers.
  const lineCounts = React.useMemo(() => {
    const totals = new Map<string, number>();
    candidates.forEach((candidate) => {
      totals.set(candidate.po_number, (totals.get(candidate.po_number) ?? 0) + 1);
    });
    const seen = new Map<string, number>();
    const positions = new Map<string, { index: number; total: number }>();
    candidates.forEach((candidate) => {
      const index = (seen.get(candidate.po_number) ?? 0) + 1;
      seen.set(candidate.po_number, index);
      positions.set(candidateKey(candidate), {
        index,
        total: totals.get(candidate.po_number) ?? 1,
      });
    });
    return positions;
  }, [candidates]);

  // Stable identity: a keystroke in a Take box must not remount the box.
  const columns = useStableColumns(candidateColumns({ lineCounts, takeFor, setTake }));

  // `listingKey={null}` below (never `PanelDataGrid`, which requires one) - see the
  // component doc comment for why. Built directly rather than through `PanelDataGrid`
  // so there is no Card/search/pagination chrome around a picker this small.
  const table = useReactTable({
    columns,
    data: candidates,
    getRowId: candidateKey,
    state: { expanded },
    onExpandedChange: setExpanded,
    getCoreRowModel: getCoreRowModel(),
    getExpandedRowModel: getExpandedRowModel(),
    columnResizeMode: 'onChange',
    enableColumnResizing: true,
  });

  const horizon = formatHorizon(linkUpTo);
  const dueAfter = isDueAfterHorizon(deliveryDate, linkUpTo);
  // The header's own number (AC-CF-24): the server's `still_to_link` (`qty - linked`),
  // read off the SAME `_unlinked_need` the candidate walk itself measures against -
  // falling back to the row's own props only while the query has not answered yet.
  const need =
    candidatesQuery.data?.still_to_link !== undefined
      ? toNumber(candidatesQuery.data.still_to_link)
      : Math.max(toNumber(qty) - toNumber(linkedQty ?? '0'), 0);
  // The row's whole capacity - what SET semantics allows the submitted takes to total,
  // whether each one is a fresh take or a line this row already held (S8): `totalTaken`
  // below is the row's PROSPECTIVE total after Link, not just what is newly added, so it
  // is measured against the row's full quantity rather than against `need`.
  //
  // `linkable_qty` (S8 review round, 17 Sep), never the bare `qty` prop: the server's
  // own `qty - bundled_qty` ceiling - `_place_on_po_set`'s own `capacity` - so a bundled
  // row's footer and enable check agree with what the POST will actually accept. Falls
  // back to `qty` only while the query has not answered yet.
  const capacity =
    candidatesQuery.data?.linkable_qty !== undefined
      ? toNumber(candidatesQuery.data.linkable_qty)
      : toNumber(qty);
  const totalTaken = candidates.reduce(
    (sum, candidate) => sum + toNumber(takeFor(candidate)),
    0,
  );
  const overTaken = totalTaken > capacity;
  const lineErrors = candidates.some((candidate) => {
    const take = toNumber(takeFor(candidate));
    return take > toNumber(candidate.remaining);
  });
  const remainder = Math.max(capacity - totalTaken, 0);
  // A Take that is typed but unreadable (bad input) is never read as 0 or as "not taken".
  const badTake = candidates.some((candidate) => takeError(takeFor(candidate)));
  const valid = totalTaken > 0 && !overTaken && !lineErrors && !badTake;

  function handleConfirm() {
    if (!valid) return;
    const allocations: OrderInquiryPoAllocation[] = candidates
      .map((candidate) =>
        candidate.kind === 'spo'
          ? {
              spo_allocation_id: candidate.spo_allocation_id ?? undefined,
              qty: takeFor(candidate) || '0',
            }
          : {
              po_line_id: candidate.po_line_id ?? undefined,
              qty: takeFor(candidate) || '0',
            },
      )
      .filter((allocation) => toNumber(allocation.qty) > 0);
    if (allocations.length === 0) return;
    // S8 review round (17 Sep): every candidate this press RENDERED, whatever its take -
    // the SET the server may retire an omitted line against. A line the dialog never
    // fetched at all (missing from this list too) is left standing rather than retired.
    const offeredLineIds = candidates.map(
      (candidate) => candidate.po_line_id ?? candidate.spo_allocation_id ?? '',
    );
    placeAllocations.mutate({ rowId, allocations, offeredLineIds }, { onSuccess: onDone });
  }

  return (
    <Dialog open onOpenChange={(next) => !next && onDone()}>
      <DialogContent className="max-h-[92vh] w-full max-w-3xl overflow-hidden">
        <DialogHeader>
          <DialogTitle>Link to a document</DialogTitle>
          <DialogDescription>
            {itemCode ?? 'This item'} - {formatInquiryQty(String(need))} still to link of{' '}
            {formatInquiryQty(qty)}. Adjust the take on any line; whatever is left unlinked
            stays demand.
          </DialogDescription>
        </DialogHeader>

        <DialogBody className="max-h-[65vh] space-y-3 overflow-y-auto">
          {/* The horizon this page links to, and whether this row is inside it (AC-LH3).
              A fact and a date, never an explanation of what a horizon is. */}
          {horizon ? (
            <div
              data-testid="link-dialog-horizon"
              className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground"
            >
              <span>Link up to {horizon}</span>
              {dueAfter ? (
                <span
                  data-testid="link-dialog-due-after"
                  className="rounded bg-amber-100 px-1.5 py-0.5 font-medium text-amber-800"
                >
                  {`Due after ${horizon}`}
                </span>
              ) : null}
            </div>
          ) : null}
          {candidatesQuery.isLoading ? (
            <div className="space-y-2">
              <Skeleton className="h-9 w-full" />
              <Skeleton className="h-9 w-full" />
              <Skeleton className="h-9 w-full" />
            </div>
          ) : candidatesQuery.isError ? (
            <Alert variant="destructive" appearance="light">
              <AlertIcon>
                <PackageSearch />
              </AlertIcon>
              <AlertContent>
                <AlertTitle>Could not load candidate lines</AlertTitle>
                <AlertDescription>
                  {candidatesQuery.error instanceof Error
                    ? candidatesQuery.error.message
                    : 'Try again in a moment.'}
                </AlertDescription>
              </AlertContent>
            </Alert>
          ) : candidates.length === 0 ? (
            <div
              data-testid="po-candidates-empty"
              className="rounded-lg border border-border px-3 py-6 text-center text-sm text-muted-foreground"
            >
              No outstanding purchase order line or SPO allocation holds this item.
            </div>
          ) : (
            <>
              <div data-testid="po-candidates-table">
                <DataGrid
                  table={table}
                  recordCount={candidates.length}
                  listingKey={null}
                  tableLayout={{ width: 'fixed', columnsResizable: true, scrollerMaxHeight: false }}
                >
                  <DataGridTable />
                </DataGrid>
              </div>

              <div
                data-testid="po-allocation-summary"
                className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-border bg-muted/30 px-3 py-2 text-xs"
              >
                <span>
                  {formatInquiryQty(String(totalTaken))} of {formatInquiryQty(String(capacity))}
                  {' '}linked
                  {remainder > 0 && !overTaken
                    ? ` - ${formatInquiryQty(String(remainder))} stays demand`
                    : ''}
                </span>
                {overTaken && (
                  <span className="font-medium text-destructive">
                    {formatInquiryQty(String(totalTaken - capacity))} more than this row needs
                  </span>
                )}
                {!overTaken && lineErrors && (
                  <span className="font-medium text-destructive">
                    A line's take is more than it has left
                  </span>
                )}
              </div>
            </>
          )}
        </DialogBody>

        <DialogFooter className="flex-col gap-2 sm:flex-row sm:justify-end">
          <Button
            type="button"
            variant="outline"
            onClick={onDone}
            disabled={placeAllocations.isPending}
          >
            Cancel
          </Button>
          <Button
            type="button"
            onClick={handleConfirm}
            disabled={!valid || placeAllocations.isPending}
          >
            {placeAllocations.isPending ? 'Linking…' : 'Link'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/**
 * The candidate grid's own columns (S8, AC-CF-26). A plain factory, not a component: the
 * caller memoizes the returned array itself (`columns` in `LinkDocumentDialog`), keyed on
 * the closures it needs fresh (`takeFor`/`setTake`, which read/write `edits`) - keeping it
 * a function rather than a hook means nothing here fights that memoization.
 */
function candidateColumns({
  lineCounts,
  takeFor,
  setTake,
}: {
  lineCounts: Map<string, { index: number; total: number }>;
  takeFor: (candidate: OrderInquiryPoCandidate) => string;
  setTake: (key: string, value: string) => void;
}): ColumnDef<OrderInquiryPoCandidate>[] {
  return [
    {
      id: 'expander',
      header: () => <span className="sr-only">Expand</span>,
      cell: ({ row }) => (
        <Button
          type="button"
          mode="icon"
          variant="ghost"
          size="sm"
          className="size-6"
          onClick={(event) => {
            event.stopPropagation();
            row.toggleExpanded();
          }}
          aria-label={row.getIsExpanded() ? 'Collapse' : 'Expand'}
          aria-expanded={row.getIsExpanded()}
        >
          {row.getIsExpanded() ? (
            <ChevronDown className="size-3.5" aria-hidden />
          ) : (
            <ChevronRight className="size-3.5" aria-hidden />
          )}
        </Button>
      ),
      size: 44,
      enableSorting: false,
      meta: {
        expandedContent: (candidate: OrderInquiryPoCandidate) => (
          <div data-testid={`po-candidate-expand-${candidateKey(candidate)}`}>
            <CandidateExpandPanel candidate={candidate} />
          </div>
        ),
      },
    },
    {
      id: 'document',
      accessorFn: (candidate) => candidate.po_number,
      header: ({ column }) => <DataGridColumnHeader title="Document" column={column} />,
      // At least 260 wide, and NEVER truncated (AC-CF-26): the whole point of the
      // conversion off the old fixed-width table was the number CS quotes getting cut
      // off ("I can't see what is the LA...").
      size: 280,
      enableSorting: false,
      cell: ({ row }) => {
        const candidate = row.original;
        const key = candidateKey(candidate);
        const line = lineCounts.get(key);
        const dedicated = Boolean(candidate.dedicated_to);
        const unattributed = Boolean(candidate.unattributed);
        const greyed = dedicated || unattributed;
        return (
          <div
            data-testid={`po-candidate-${key}`}
            className={cn('min-w-0 py-1', greyed && 'rounded bg-muted/40 text-muted-foreground')}
          >
            <span className="flex min-w-0 flex-wrap items-baseline gap-x-1.5 gap-y-0.5">
              <span className="whitespace-normal break-words font-medium tabular-nums">
                {candidate.po_number}
                {candidate.line_label ? ` ${candidate.line_label}` : ''}
              </span>
              <span className="shrink-0 rounded-sm bg-muted px-1 py-0.5 text-[10px] font-medium uppercase text-muted-foreground">
                {candidate.kind}
              </span>
              {line && line.total > 1 && (
                <span className="shrink-0 text-[10px] text-muted-foreground">
                  {`line ${line.index} of ${line.total}`}
                </span>
              )}
            </span>
            <span className="mt-0.5 flex flex-wrap gap-1">
              {candidate.cited && (
                <span className="inline-block rounded-sm bg-amber-500/15 px-1.5 py-0.5 text-[10px] font-medium text-amber-700">
                  Cited
                </span>
              )}
              {dedicated && (
                <span
                  data-testid={`po-candidate-dedicated-${key}`}
                  className="inline-block rounded-sm bg-muted px-1.5 py-0.5 text-[10px] font-medium text-muted-foreground"
                >
                  {`Dedicated to SO ${candidate.dedicated_to}`}
                </span>
              )}
              {unattributed && (
                <span
                  data-testid={`po-candidate-unattributed-${key}`}
                  className="inline-block rounded-sm bg-muted px-1.5 py-0.5 text-[10px] font-medium text-muted-foreground"
                >
                  Unattributed - link manually
                </span>
              )}
              {toNumber(candidate.current_take ?? '0') > 0 && (
                <span
                  data-testid={`po-candidate-current-${key}`}
                  className="inline-block rounded-sm bg-emerald-500/15 px-1.5 py-0.5 text-[10px] font-medium text-emerald-700"
                >
                  {`Current ${formatInquiryQty(candidate.current_take as string)}`}
                </span>
              )}
              {toNumber(candidate.default_take) > 0 && (
                <span className="inline-block rounded-sm bg-primary/10 px-1.5 py-0.5 text-[10px] font-medium text-primary">
                  Cascade take {formatInquiryQty(candidate.default_take)}
                </span>
              )}
            </span>
          </div>
        );
      },
      meta: { headerTitle: 'Document' },
    },
    {
      id: 'location',
      accessorFn: (candidate) => candidate.location ?? '',
      header: ({ column }) => <DataGridColumnHeader title="Where" column={column} />,
      size: 140,
      enableSorting: false,
      cell: ({ row }) => (
        <div className="min-w-0">
          <span className="block truncate" title={row.original.location ?? 'Not stated'}>
            {row.original.location ?? <span className="text-muted-foreground">Not stated</span>}
          </span>
          <span className="block truncate text-[10px] text-muted-foreground">
            {TIER_LABEL[row.original.tier] ?? 'Elsewhere'}
          </span>
        </div>
      ),
      meta: { headerTitle: 'Where' },
    },
    {
      id: 'supplier',
      accessorFn: (candidate) => candidate.supplier_name ?? '',
      header: ({ column }) => <DataGridColumnHeader title="Supplier" column={column} />,
      size: 170,
      enableSorting: false,
      cell: ({ row }) => (
        <span className="block truncate" title={row.original.supplier_name ?? 'Not stated'}>
          {row.original.supplier_name ?? (
            <span className="text-muted-foreground">Not stated</span>
          )}
        </span>
      ),
      meta: { headerTitle: 'Supplier' },
    },
    {
      id: 'issue_date',
      accessorFn: (candidate) => candidate.issue_date ?? '',
      header: ({ column }) => <DataGridColumnHeader title="Issued" column={column} />,
      size: 110,
      enableSorting: false,
      cell: ({ row }) =>
        row.original.issue_date ? (
          formatDateInMalaysia(row.original.issue_date)
        ) : (
          <span className="text-muted-foreground">No date</span>
        ),
      meta: { headerTitle: 'Issued' },
    },
    {
      id: 'expected_date',
      accessorFn: (candidate) => candidate.expected_date ?? '',
      header: ({ column }) => <DataGridColumnHeader title="Expected" column={column} />,
      size: 110,
      enableSorting: false,
      cell: ({ row }) =>
        row.original.expected_date ? (
          formatDateInMalaysia(row.original.expected_date)
        ) : (
          <span className="text-muted-foreground">No date</span>
        ),
      meta: { headerTitle: 'Expected' },
    },
    {
      id: 'remaining',
      accessorFn: (candidate) => Number.parseFloat(candidate.remaining) || 0,
      header: ({ column }) => (
        <DataGridColumnHeader title="Remaining" column={column} className="justify-end" />
      ),
      size: 100,
      enableSorting: false,
      cell: ({ row }) => (
        <span className="block text-end font-medium tabular-nums">
          {formatInquiryQty(row.original.remaining)}
        </span>
      ),
      meta: { headerTitle: 'Remaining' },
    },
    {
      id: 'take',
      header: ({ column }) => (
        <DataGridColumnHeader title="Take" column={column} className="justify-end" />
      ),
      size: 120,
      enableSorting: false,
      cell: ({ row }) => {
        const candidate = row.original;
        const key = candidateKey(candidate);
        const take = takeFor(candidate);
        const overLine = toNumber(take) > toNumber(candidate.remaining);
        return (
          <NumberInput
            min={0}
            step="any"
            value={take}
            error={takeError(take)}
            onChange={(event) => setTake(key, event.target.value)}
            aria-label={`Take off ${candidate.po_number}`}
            className={cn('h-7 text-end text-xs', overLine && 'border-destructive')}
          />
        );
      },
      meta: { headerTitle: 'Take' },
    },
  ];
}

/**
 * The candidate's expand (the captain, 20 Aug): the PO line's OWN figures, and every
 * OTHER row already tagged to it - the evidence behind `already_tagged` / `remaining`,
 * not just their totals. Same visual pattern as `GroupMembersPanel`'s drills elsewhere
 * (`scm/reorder`) - a small nested table under the row, no page-wide overflow.
 */
function CandidateExpandPanel({ candidate }: { candidate: OrderInquiryPoCandidate }) {
  const unitPrice = formatCandidateUnitPrice(candidate.unit_cost, candidate.currency);

  return (
    <div className="px-4 py-3">
      <dl className="grid grid-cols-2 gap-x-6 gap-y-1 text-2xs sm:grid-cols-5">
        <ExpandField label="Qty ordered" value={formatInquiryQty(candidate.qty_ordered)} />
        <ExpandField label="Qty received" value={formatInquiryQty(candidate.qty_received)} />
        <ExpandField label="Remaining" value={formatInquiryQty(candidate.remaining)} />
        <ExpandField
          label="Expected"
          value={
            candidate.expected_date ? formatDateInMalaysia(candidate.expected_date) : 'No date'
          }
        />
        <ExpandField label="Where" value={candidate.location ?? 'Not stated'} />
        <ExpandField
          label="Location fit"
          value={TIER_LABEL[candidate.tier] ?? 'Elsewhere'}
        />
        <ExpandField label="Unit price" value={unitPrice ?? 'No price on file'} />
      </dl>

      <div className="mt-3">
        <p className="mb-1 text-2xs font-medium text-muted-foreground">
          Already linked on this line
        </p>
        {candidate.claims.length === 0 ? (
          <p className="text-2xs text-muted-foreground">
            No other row is linked to this line yet.
          </p>
        ) : (
          <div className="overflow-x-auto overscroll-x-contain">
            <table className="w-max border-separate border-spacing-0 text-2xs">
              <thead>
                <tr className="text-muted-foreground">
                  <th className="px-2 py-1 text-start font-medium">SO number</th>
                  <th className="px-2 py-1 text-start font-medium">Item code</th>
                  <th className="px-2 py-1 text-end font-medium">Qty</th>
                  <th className="px-2 py-1 text-start font-medium">Linked on</th>
                </tr>
              </thead>
              <tbody>
                {candidate.claims.map((claim, index) => (
                  <tr key={`${claim.so_number ?? ''}-${index}`} className="border-t border-border/60">
                    <td className="px-2 py-1">{claim.so_number ?? '-'}</td>
                    <td className="px-2 py-1">{claim.item_code ?? '-'}</td>
                    <td className="px-2 py-1 text-end tabular-nums">
                      {formatInquiryQty(claim.qty)}
                    </td>
                    <td className="px-2 py-1">
                      {claim.placed_date ? formatDateInMalaysia(claim.placed_date) : '-'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}

function ExpandField({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0">
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="truncate font-medium tabular-nums">{value}</dd>
    </div>
  );
}
