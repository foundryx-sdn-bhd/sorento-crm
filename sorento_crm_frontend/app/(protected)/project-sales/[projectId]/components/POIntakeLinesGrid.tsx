'use client';

import * as React from 'react';
import { ColumnDef, getCoreRowModel, useReactTable } from '@tanstack/react-table';
import { Ban, Check, Loader2, Pencil, RotateCcw, StickyNote, X } from 'lucide-react';
import { toast } from '@/lib/toast';
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
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { DataGrid } from '@/components/ui/data-grid';
import { DataGridColumnHeader } from '@/components/ui/data-grid-column-header';
import { DataGridTable } from '@/components/ui/data-grid-table';
import { Input } from '@/components/ui/input';
import {
  Popover,
  PopoverContent,
  PopoverPortal,
  PopoverTrigger,
} from '@/components/ui/popover';
import { Skeleton } from '@/components/ui/skeleton';
import { Textarea } from '@/components/ui/textarea';
import { SearchableSelect } from '@/components/common/SearchableSelect';
import { ToggleGroup, ToggleGroupItem } from '@/components/ui/toggle-group';
// The shared products `/select` mapper. Its name says "variant" because that screen needed
// it first; the endpoint and the shape are the generic ones.
import { getProductsForVariantSelect } from '@/app/(protected)/master-data-management/products/services/productService';
import type {
  POAnnotation,
  POAnnotationEditBody,
  POLineUpdateBody,
  POVersionLine,
} from '../../_shared/types/poIntake.types';
import { PO_INTERPRETATION_LABELS } from '../../_shared/types/poIntake.types';
import { formatMyrExact, formatQty, isDecimalString, multiplyMoney } from '../../_shared/lib/money';
import { describeAnnotationEffect } from './POIntakeAnnotationsGrid';
import { POIntakeAnnotationEditDialog } from './POIntakeAnnotationEditDialog';

export interface POIntakeLinesGridHandle {
  focusLine: (lineId: string) => void;
  /** True if a line still had a note to review and the reader was sent to it. */
  focusFirstUnreviewedAnnotation: () => boolean;
}

/**
 * Column preferences are keyed on this, not on the pathname DataGrid would fall back to:
 * the path carries the version id, so the fallback would write one preferences row per
 * uploaded document instead of one per person. `<perm_slug>::<stable_id>`, per
 * docs/LISTING-COLUMN-PREFERENCES.md.
 */
export const PO_INTAKE_LINES_LISTING_KEY = 'projects.projects.view::po-intake-lines';

interface Props {
  lines: POVersionLine[];
  readOnly: boolean;
  savingLineIds: string[];
  focusedLineId: string | null;
  onFocusLine: (line: POVersionLine) => void;
  onUpdateLine: (lineId: string, body: POLineUpdateBody) => Promise<void>;
  /** Every note the pencil left, including the ones naming no line (those are ignored here). */
  annotations: POAnnotation[];
  savingAnnotationIds: string[];
  onShowPage: (page: number) => void;
  onAcceptAnnotation: (annotationId: string, note?: string | null) => Promise<void>;
  onEditAnnotation: (annotationId: string, body: POAnnotationEditBody) => Promise<void>;
  onRejectAnnotation: (annotationId: string, note: string) => Promise<void>;
  /**
   * S6-3: the grid opens on "Need attention" while the version is unconfirmed, and on every
   * line once it is - a confirmed PO is a record to check against, not a queue to work.
   */
  defaultFlaggedOnly?: boolean;
}

/** The short marker on the row: what the pencil is asking for, not why. */
function annotationBadgeLabel(note: POAnnotation): string {
  const json = (note.interpretation_json ?? {}) as Record<string, unknown>;
  switch (note.interpretation) {
    case 'cancel_line':
      return 'Cancel line';
    case 'amend_code': {
      const code = typeof json.code === 'string' ? json.code : '';
      return code ? `Amend code to ${code}` : 'Amend code';
    }
    case 'amend_description':
      return 'Amend description';
    case 'successor_po': {
      const poNumber = typeof json.po_number === 'string' ? json.po_number : '';
      return poNumber ? `Replaced by ${poNumber}` : 'Names a replacement PO';
    }
    case 'signature':
      return 'Signature';
    default:
      return PO_INTERPRETATION_LABELS[note.interpretation] ?? 'Note';
  }
}

/** A line our arithmetic disagrees with, one with no product, or one that was cancelled. */
export function isFlaggedLine(line: POVersionLine): boolean {
  return !line.arithmetic_ok || !line.resolved_product_id || line.is_cancelled;
}

/**
 * A line that wants a person to do something. A cancelled line is deliberately NOT one of
 * these: somebody already decided about it by accepting a card, so counting it as work
 * outstanding would mean accepting a cancellation ADDS to the pile.
 */
export function lineNeedsAttention(line: POVersionLine): boolean {
  return !line.is_cancelled && (!line.arithmetic_ok || !line.resolved_product_id);
}

/**
 * The notes that hold Confirm: still proposed AND naming a line on this version. The server's
 * `blocking_annotations` (`project_po_confirm.py`) is the same rule, so the button, the
 * "Need attention" rows and the server's refusal always agree (owner re-test 25 Sep 2026: the
 * server once counted 11 notes this screen did not show). A note naming no line here has no
 * row to be reviewed from, so it never blocks.
 */
export function blockingNotes(
  annotations: POAnnotation[],
  lines: Pick<POVersionLine, 'line_no'>[],
): POAnnotation[] {
  const lineNos = new Set(lines.map((line) => line.line_no));
  return annotations.filter(
    (note) =>
      note.state === 'proposed' && note.refers_to_lines.some((lineNo) => lineNos.has(lineNo)),
  );
}

/**
 * The 52 lines, editable in place.
 *
 * Cells are UNCONTROLLED and commit on blur. A controlled input would re-render the whole
 * grid on every keystroke, and a person correcting a misread quantity is typing into a table
 * of fifty-two rows. Escape puts the cell back to what the server holds.
 *
 * Numeric cells are plain text inputs, not native number inputs: money and quantity are strings from
 * the API to the API, and a number input invites the float round trip the contract forbids.
 *
 * The real task on this document is three exceptions out of fifty-two rows, so the flagged
 * lines can be shown on their own. That filter opens ON while the version is unconfirmed
 * (S6-3, `defaultFlaggedOnly`) - the exceptions are the work; "All lines" is one click away
 * for reconciling a total against the whole document.
 */
export const POIntakeLinesGrid = React.forwardRef<POIntakeLinesGridHandle, Props>(
  function POIntakeLinesGrid(
    {
      lines,
      readOnly,
      savingLineIds,
      focusedLineId,
      onFocusLine,
      onUpdateLine,
      annotations,
      savingAnnotationIds,
      onShowPage,
      onAcceptAnnotation,
      onEditAnnotation,
      onRejectAnnotation,
      defaultFlaggedOnly,
    },
    ref,
  ) {
    const rowRefs = React.useRef<Record<string, HTMLElement | null>>({});
    const [confirming, setConfirming] = React.useState<{
      kind: 'cancel' | 'clear-product';
      line: POVersionLine;
    } | null>(null);
    const [flaggedOnly, setFlaggedOnly] = React.useState(defaultFlaggedOnly ?? false);
    // A row asked for while it is filtered out cannot be scrolled to until it mounts.
    const pendingFocusId = React.useRef<string | null>(null);
    // Which line's notes popover is open, if any - one at a time, and closed by default. Set
    // from "Review them" (the header's call to action) landing on a note-only line, never on
    // an ordinary row click, so a popover opens only when the reader asked to review notes.
    const [openNotesLineId, setOpenNotesLineId] = React.useState<string | null>(null);
    const pendingOpenNotesId = React.useRef<string | null>(null);

    // Accept / reject / edit dialogs are shared across every row, the same way the old
    // "Handwriting" card shared one set of dialogs across every note: a note naming three
    // lines is still one thing to decide about, decided once.
    const [acceptingAnnotation, setAcceptingAnnotation] = React.useState<{
      note: POAnnotation;
      lineId: string;
    } | null>(null);
    const [rejectingAnnotation, setRejectingAnnotation] = React.useState<{
      note: POAnnotation;
      lineId: string;
    } | null>(null);
    const [editingAnnotation, setEditingAnnotation] = React.useState<POAnnotation | null>(
      null,
    );
    const [rejectNote, setRejectNote] = React.useState('');
    // Which note (and which row it was cleared from) to watch for, so that once the parent's
    // data confirms it is no longer proposed, focus moves on to the next warning on its own
    // rather than leaving the reader to scroll and find it.
    const lastActionedAnnotationId = React.useRef<string | null>(null);
    const lastActionedLineId = React.useRef<string | null>(null);

    // Notes that name at least one line, grouped by the line number they name. A note naming
    // three lines lives in three buckets, so it shows, and clears, on every one of them.
    const annotationsByLineNo = React.useMemo(() => {
      const map = new Map<number, POAnnotation[]>();
      for (const note of annotations) {
        for (const lineNo of note.refers_to_lines) {
          const bucket = map.get(lineNo);
          if (bucket) bucket.push(note);
          else map.set(lineNo, [note]);
        }
      }
      return map;
    }, [annotations]);

    // "Identified" (S6-3): an arithmetic mismatch, an unresolved product, a cancellation, or
    // a handwritten note still waiting on a decision - everything a person has to look at
    // before this document can be trusted, not only what `isFlaggedLine` alone catches.
    const flagged = React.useMemo(
      () =>
        lines.filter(
          (line) =>
            isFlaggedLine(line) ||
            (annotationsByLineNo.get(line.line_no) ?? []).some(
              (note) => note.state === 'proposed',
            ),
        ),
      [lines, annotationsByLineNo],
    );
    const visibleLines = flaggedOnly ? flagged : lines;
    const nothingLeftToShow = flaggedOnly && visibleLines.length === 0;
    // When the filter has emptied itself the way back lives in the all-clear panel, so the
    // toolbar toggle stands down rather than offering the same thing twice.
    const showFilterToggle = (flagged.length > 0 || flaggedOnly) && !nothingLeftToShow;

    const linesWithUnreviewedAnnotations = React.useMemo(
      () =>
        lines
          .filter((line) =>
            (annotationsByLineNo.get(line.line_no) ?? []).some(
              (note) => note.state === 'proposed',
            ),
          )
          .sort((a, b) => a.line_no - b.line_no),
      [lines, annotationsByLineNo],
    );

    const scrollToLine = React.useCallback((lineId: string) => {
      // jsdom implements no scrollIntoView, hence the optional call.
      rowRefs.current[lineId]?.scrollIntoView?.({
        block: 'center',
        behavior: 'smooth',
      });
    }, []);

    const focusLineInternal = React.useCallback(
      (lineId: string, options?: { openNotes?: boolean }) => {
        const line = lines.find((item) => item.id === lineId);
        if (!line) return;
        onFocusLine(line);
        // The banner and the handwriting notes both point at specific lines, and a note can
        // name a perfectly healthy one. Landing on it has to work with the filter on, so the
        // filter gives way rather than swallowing the jump - but only when the line is not
        // already one of the ones "Need attention" shows (N1: a note-only line is already
        // visible there, and flipping to "All lines" on its own call to action defeats the
        // guided view).
        if (flaggedOnly && !flagged.some((item) => item.id === lineId)) {
          pendingFocusId.current = lineId;
          if (options?.openNotes) pendingOpenNotesId.current = lineId;
          setFlaggedOnly(false);
          return;
        }
        scrollToLine(lineId);
        if (options?.openNotes) setOpenNotesLineId(lineId);
      },
      [lines, onFocusLine, flaggedOnly, flagged, scrollToLine],
    );

    // Where "clear the warning and move on" goes next: the first line, after the one just
    // cleared, that still has a note nobody has looked at. Wraps back to the top of the list
    // rather than stopping, since the document does not end where the reader started.
    const goToNextUnreviewed = React.useCallback(
      (afterLineId: string | null) => {
        if (linesWithUnreviewedAnnotations.length === 0) return;
        const anchor = lines.find((item) => item.id === afterLineId);
        const anchorLineNo = anchor?.line_no ?? -Infinity;
        const next =
          linesWithUnreviewedAnnotations.find((line) => line.line_no > anchorLineNo) ??
          linesWithUnreviewedAnnotations[0];
        focusLineInternal(next.id);
      },
      [linesWithUnreviewedAnnotations, lines, focusLineInternal],
    );

    React.useImperativeHandle(ref, () => ({
      focusLine: focusLineInternal,
      focusFirstUnreviewedAnnotation: () => {
        if (linesWithUnreviewedAnnotations.length === 0) return false;
        // "Review them" (the header link) opens the first unreviewed line's popover
        // directly, rather than merely scrolling to a row the reader still has to click.
        focusLineInternal(linesWithUnreviewedAnnotations[0].id, { openNotes: true });
        return true;
      },
    }));

    React.useEffect(() => {
      const pending = pendingFocusId.current;
      if (!pending) return;
      pendingFocusId.current = null;
      scrollToLine(pending);
      if (pendingOpenNotesId.current === pending) {
        pendingOpenNotesId.current = null;
        setOpenNotesLineId(pending);
      }
    }, [flaggedOnly, scrollToLine]);

    // Runs after the parent's data confirms the note just accepted or rejected is no longer
    // proposed (a fresh fetch, not the click itself), and only then moves focus on: acting on
    // stale data would send the reader to a line whose warning has not actually cleared yet.
    React.useEffect(() => {
      const noteId = lastActionedAnnotationId.current;
      if (!noteId) return;
      const stillProposed = annotations.some(
        (note) => note.id === noteId && note.state === 'proposed',
      );
      if (stillProposed) return;
      const fromLineId = lastActionedLineId.current;
      lastActionedAnnotationId.current = null;
      lastActionedLineId.current = null;
      // The just-resolved line's popover has nothing left to show - close it rather than
      // leaving it open over an empty indicator while focus moves on to the next one.
      setOpenNotesLineId(null);
      goToNextUnreviewed(fromLineId);
    }, [annotations, goToNextUnreviewed]);

    const openAcceptAnnotation = React.useCallback(
      (note: POAnnotation, lineId: string) => setAcceptingAnnotation({ note, lineId }),
      [],
    );
    const openRejectAnnotation = React.useCallback((note: POAnnotation, lineId: string) => {
      setRejectNote('');
      setRejectingAnnotation({ note, lineId });
    }, []);

    const fetchProducts = React.useCallback(async (query: string) => {
      const rows = await getProductsForVariantSelect(query || undefined);
      return rows.map((row) => ({
        value: row.id,
        label: row.product_code,
        description: row.product_name,
      }));
    }, []);

    const commit = React.useCallback(
      (line: POVersionLine, body: POLineUpdateBody) => {
        void onUpdateLine(line.id, body);
      },
      [onUpdateLine],
    );

    const columns = React.useMemo<ColumnDef<POVersionLine>[]>(() => {
      const cellWrap = (
        line: POVersionLine,
        children: React.ReactNode,
        first = false,
      ) => (
        <div
          ref={
            first
              ? (element) => {
                  rowRefs.current[line.id] = element;
                }
              : undefined
          }
          className="min-w-0"
          onFocusCapture={() => onFocusLine(line)}
          onClick={() => onFocusLine(line)}
        >
          {children}
        </div>
      );

      return [
        {
          accessorKey: 'line_no',
          header: ({ column }) => <DataGridColumnHeader title="#" column={column} />,
          cell: ({ row }) =>
            cellWrap(
              row.original,
              <span className="flex items-center gap-1 text-sm tabular-nums">
                {row.original.line_no}
                {savingLineIds.includes(row.original.id) && (
                  <Loader2
                    className="size-3 animate-spin text-muted-foreground"
                    aria-hidden
                  />
                )}
              </span>,
              true,
            ),
          size: 64,
          minSize: 56,
          meta: { headerTitle: '#', skeleton: <Skeleton className="h-4 w-6" /> },
        },
        {
          accessorKey: 'stock_code_raw',
          header: ({ column }) => (
            <DataGridColumnHeader title="Code on the PO" column={column} />
          ),
          cell: ({ row }) =>
            cellWrap(
              row.original,
              <CellInput
                line={row.original}
                field="stock_code_raw"
                value={row.original.stock_code_raw ?? ''}
                readOnly={readOnly}
                label={`Code on line ${row.original.line_no}`}
                onCommit={(value) =>
                  commit(row.original, { stock_code_raw: value || null })
                }
              />,
            ),
          size: 160,
          minSize: 120,
          meta: {
            headerTitle: 'Code on the PO',
            skeleton: <Skeleton className="h-4 w-20" />,
          },
        },
        {
          accessorKey: 'description_raw',
          header: ({ column }) => (
            <DataGridColumnHeader title="Description" column={column} />
          ),
          cell: ({ row }) =>
            cellWrap(
              row.original,
              <CellInput
                line={row.original}
                field="description_raw"
                value={row.original.description_raw ?? ''}
                readOnly={readOnly}
                label={`Description on line ${row.original.line_no}`}
                onCommit={(value) =>
                  commit(row.original, { description_raw: value || null })
                }
              />,
            ),
          size: 260,
          minSize: 160,
          meta: {
            headerTitle: 'Description',
            skeleton: <Skeleton className="h-4 w-40" />,
          },
        },
        {
          accessorKey: 'qty',
          header: ({ column }) => <DataGridColumnHeader title="Qty" column={column} />,
          cell: ({ row }) =>
            cellWrap(
              row.original,
              <CellInput
                line={row.original}
                field="qty"
                value={row.original.qty}
                numeric
                readOnly={readOnly}
                label={`Quantity on line ${row.original.line_no}`}
                onCommit={(value) => commit(row.original, { qty: value })}
              />,
            ),
          size: 92,
          minSize: 72,
          meta: {
            headerTitle: 'Qty',
            skeleton: <Skeleton className="h-4 w-10" />,
          },
        },
        {
          accessorKey: 'uom_raw',
          header: ({ column }) => <DataGridColumnHeader title="UOM" column={column} />,
          cell: ({ row }) =>
            cellWrap(
              row.original,
              <CellInput
                line={row.original}
                field="uom_raw"
                value={row.original.uom_raw ?? ''}
                readOnly={readOnly}
                label={`UOM on line ${row.original.line_no}`}
                onCommit={(value) => commit(row.original, { uom_raw: value || null })}
              />,
            ),
          size: 88,
          minSize: 72,
          meta: {
            headerTitle: 'UOM',
            skeleton: <Skeleton className="h-4 w-10" />,
          },
        },
        {
          accessorKey: 'unit_price',
          header: ({ column }) => (
            <DataGridColumnHeader title="Unit price" column={column} />
          ),
          cell: ({ row }) =>
            cellWrap(
              row.original,
              <CellInput
                line={row.original}
                field="unit_price"
                value={row.original.unit_price}
                numeric
                readOnly={readOnly}
                label={`Unit price on line ${row.original.line_no}`}
                onCommit={(value) => commit(row.original, { unit_price: value })}
              />,
            ),
          size: 116,
          minSize: 96,
          meta: {
            headerTitle: 'Unit price',
            skeleton: <Skeleton className="h-4 w-14" />,
          },
        },
        {
          accessorKey: 'amount',
          header: ({ column }) => <DataGridColumnHeader title="Amount" column={column} />,
          cell: ({ row }) =>
            cellWrap(
              row.original,
              <CellInput
                line={row.original}
                field="amount"
                value={row.original.amount}
                numeric
                invalid={!row.original.arithmetic_ok}
                readOnly={readOnly}
                label={`Amount on line ${row.original.line_no}`}
                onCommit={(value) => commit(row.original, { amount: value })}
              />,
            ),
          size: 124,
          minSize: 100,
          meta: {
            headerTitle: 'Amount',
            skeleton: <Skeleton className="h-4 w-16" />,
          },
        },
        {
          id: 'check',
          header: ({ column }) => <DataGridColumnHeader title="Flag" column={column} />,
          cell: ({ row }) => {
            const line = row.original;
            const expected = multiplyMoney(line.qty, line.unit_price);
            const lineNotes = annotationsByLineNo.get(line.line_no) ?? [];
            return cellWrap(
              line,
              <div className="flex flex-wrap items-center gap-1">
                {!line.arithmetic_ok && (
                  <Badge
                    variant="destructive"
                    className="text-[11px]"
                    title={
                      expected
                        ? `Quantity times unit price is ${formatMyrExact(expected)}`
                        : undefined
                    }
                  >
                    {expected
                      ? `Should be ${formatMyrExact(expected)}`
                      : 'Does not multiply out'}
                  </Badge>
                )}
                {line.is_cancelled && (
                  <Badge variant="secondary" className="text-[11px]">
                    Cancelled
                  </Badge>
                )}
                {!line.resolved_product_id && (
                  <Badge variant="outline" className="text-[11px]">
                    No product
                  </Badge>
                )}
                {line.resolution_source === 'manual' && (
                  <Badge variant="outline" className="text-[11px]">
                    Matched by hand
                  </Badge>
                )}
                <LineNotesIndicator
                  line={line}
                  // A viewer without edit rights has nothing to do about a note, but it
                  // must still show here, muted - otherwise a read-only viewer sees no
                  // pending handwriting at all. An editor only ever needs the ones still
                  // waiting on a decision; once resolved, a note has nothing left to show.
                  notes={
                    readOnly ? lineNotes : lineNotes.filter((note) => note.state === 'proposed')
                  }
                  readOnly={readOnly}
                  open={openNotesLineId === line.id}
                  onOpenChange={(next) => setOpenNotesLineId(next ? line.id : null)}
                  savingAnnotationIds={savingAnnotationIds}
                  onShowPage={onShowPage}
                  onAccept={(note) => openAcceptAnnotation(note, line.id)}
                  onEdit={(note) => setEditingAnnotation(note)}
                  onReject={(note) => openRejectAnnotation(note, line.id)}
                />
              </div>,
            );
          },
          size: 190,
          minSize: 130,
          meta: {
            headerTitle: 'Flag',
            skeleton: <Skeleton className="h-4 w-20" />,
          },
        },
        {
          id: 'product',
          header: ({ column }) => (
            <DataGridColumnHeader title="Our product" column={column} />
          ),
          cell: ({ row }) => {
            const line = row.original;
            const selected = line.resolved_product_id
              ? {
                  value: line.resolved_product_id,
                  label: line.resolved_product_code ?? 'Matched product',
                }
              : undefined;
            return cellWrap(
              line,
              readOnly ? (
                <span
                  className="block truncate text-sm"
                  title={line.resolved_product_code ?? ''}
                >
                  {line.resolved_product_code ?? 'Not matched'}
                </span>
              ) : (
                <>
                  {/* SearchableSelect forwards `id`, not arbitrary aria props, so the
                      accessible name has to come from a real label. */}
                  <label
                    className="sr-only"
                    htmlFor={`po-line-${line.line_no}-product`}
                  >
                    {`Product on line ${line.line_no}`}
                  </label>
                  <SearchableSelect
                    id={`po-line-${line.line_no}-product`}
                    value={line.resolved_product_id ?? ''}
                    size="sm"
                    clearable
                    fetchOptions={fetchProducts}
                    selectedOption={selected}
                    placeholder="Not matched"
                    emptyMessage="No products match"
                    onChange={(value) => {
                      if (!value && line.resolved_product_id) {
                        setConfirming({ kind: 'clear-product', line });
                        return;
                      }
                      if (value !== (line.resolved_product_id ?? '')) {
                        commit(line, { resolved_product_id: value || null });
                      }
                    }}
                  />
                </>
              ),
            );
          },
          size: 210,
          minSize: 150,
          meta: {
            headerTitle: 'Our product',
            skeleton: <Skeleton className="h-4 w-24" />,
          },
        },
        {
          id: 'actions',
          header: () => <span className="sr-only">Actions</span>,
          cell: ({ row }) => {
            const line = row.original;
            if (readOnly) return null;
            return cellWrap(
              line,
              line.is_cancelled ? (
                <Button
                  type="button"
                  mode="icon"
                  variant="ghost"
                  size="sm"
                  aria-label={`Restore line ${line.line_no}`}
                  onClick={() => commit(line, { is_cancelled: false })}
                >
                  <RotateCcw className="size-3.5" />
                </Button>
              ) : (
                <Button
                  type="button"
                  mode="icon"
                  variant="ghost"
                  size="sm"
                  aria-label={`Cancel line ${line.line_no}`}
                  onClick={() => setConfirming({ kind: 'cancel', line })}
                >
                  <Ban className="size-3.5 text-destructive" />
                </Button>
              ),
            );
          },
          size: 68,
          minSize: 56,
          meta: {
            headerTitle: 'Actions',
            skeleton: <Skeleton className="h-4 w-6" />,
          },
        },
      ];
    }, [
      annotationsByLineNo,
      commit,
      fetchProducts,
      onFocusLine,
      onShowPage,
      openAcceptAnnotation,
      openNotesLineId,
      openRejectAnnotation,
      readOnly,
      savingAnnotationIds,
      savingLineIds,
    ]);

    const table = useReactTable({
      columns,
      data: visibleLines,
      getRowId: (row) => row.id,
      getCoreRowModel: getCoreRowModel(),
      columnResizeMode: 'onChange',
    });

    return (
      <div className="min-w-0 space-y-2">
        {showFilterToggle && (
          <div className="flex justify-start">
            <ToggleGroup
              type="single"
              variant="outline"
              size="sm"
              value={flaggedOnly ? 'flagged' : 'all'}
              onValueChange={(next) => next && setFlaggedOnly(next === 'flagged')}
            >
              <ToggleGroupItem value="flagged" className="px-3">
                {`Need attention (${flagged.length})`}
              </ToggleGroupItem>
              <ToggleGroupItem value="all" className="px-3">
                {`All lines (${lines.length})`}
              </ToggleGroupItem>
            </ToggleGroup>
          </div>
        )}

        {nothingLeftToShow ? (
          <div className="rounded-lg border border-emerald-500/40 bg-emerald-50 px-6 py-10 text-center dark:bg-emerald-950/30">
            <h3 className="text-sm font-semibold text-emerald-900 dark:text-emerald-300">
              Nothing left to fix
            </h3>
            <Button
              type="button"
              variant="outline"
              className="mt-4"
              onClick={() => setFlaggedOnly(false)}
            >
              {`All lines (${lines.length})`}
            </Button>
          </div>
        ) : (
          <DataGrid
            table={table}
            recordCount={visibleLines.length}
            isLoading={false}
            listingKey={PO_INTAKE_LINES_LISTING_KEY}
            tableLayout={{
              width: 'fixed',
              columnsResizable: true,
              // The grid's OWN scroller (`data-grid-scroller`) carries both axes - a
              // Radix `ScrollArea` wrapped around `DataGridTable` used to sit here
              // instead, but that gives the table a `display: table` ancestor which
              // shrink-fits, so the scroller never measured an overflow and the Amount
              // column clipped with no way to reach it (owner hand test, 25 Sep 2026,
              // item 1). A plain height string keeps the same vertical budget the
              // ScrollArea's viewport used to cap at, with the horizontal scroll the
              // scroller already brings for free.
              scrollerMaxHeight: 'max-h-[calc(100vh-14rem)] overflow-y-auto',
            }}
          >
            <div className="min-w-0 rounded-lg border border-border">
              <DataGridTable />
            </div>
          </DataGrid>
        )}

        <AlertDialog
          open={Boolean(confirming)}
          onOpenChange={(next) => !next && setConfirming(null)}
        >
          <AlertDialogContent className="max-h-[85vh] overflow-y-auto">
            <AlertDialogHeader>
              <AlertDialogTitle>
                {confirming?.kind === 'cancel'
                  ? `Cancel line ${confirming.line.line_no}?`
                  : 'Remove the matched product?'}
              </AlertDialogTitle>
              <AlertDialogDescription>
                {confirming?.kind === 'cancel'
                  ? `${describeLine(confirming.line)} stays on the record, marked cancelled, and is left out of our total. This action cannot be undone from the document.`
                  : confirming
                    ? `Line ${confirming.line.line_no} goes back to unmatched, and nothing on it will resolve to ${confirming.line.resolved_product_code ?? 'our catalogue'}.`
                    : ''}
              </AlertDialogDescription>
            </AlertDialogHeader>
            <AlertDialogFooter>
              <AlertDialogCancel>Keep it</AlertDialogCancel>
              <AlertDialogAction
                className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
                onClick={() => {
                  if (!confirming) return;
                  if (confirming.kind === 'cancel') {
                    commit(confirming.line, { is_cancelled: true });
                  } else {
                    commit(confirming.line, { resolved_product_id: null });
                  }
                  setConfirming(null);
                }}
              >
                {confirming?.kind === 'cancel' ? 'Cancel the line' : 'Remove it'}
              </AlertDialogAction>
            </AlertDialogFooter>
          </AlertDialogContent>
        </AlertDialog>

        <AlertDialog
          open={Boolean(acceptingAnnotation)}
          onOpenChange={(next) => !next && setAcceptingAnnotation(null)}
        >
          <AlertDialogContent className="max-h-[85vh] overflow-y-auto">
            <AlertDialogHeader>
              <AlertDialogTitle>Accept this note?</AlertDialogTitle>
              <AlertDialogDescription>
                {acceptingAnnotation
                  ? describeAnnotationEffect(acceptingAnnotation.note, lines)
                  : ''}
              </AlertDialogDescription>
            </AlertDialogHeader>
            <AlertDialogFooter>
              <AlertDialogCancel>Not yet</AlertDialogCancel>
              <AlertDialogAction
                className={
                  acceptingAnnotation?.note.interpretation === 'cancel_line'
                    ? 'bg-destructive text-destructive-foreground hover:bg-destructive/90'
                    : undefined
                }
                onClick={() => {
                  if (acceptingAnnotation) {
                    lastActionedAnnotationId.current = acceptingAnnotation.note.id;
                    lastActionedLineId.current = acceptingAnnotation.lineId;
                    void onAcceptAnnotation(acceptingAnnotation.note.id);
                  }
                  setAcceptingAnnotation(null);
                }}
              >
                {acceptingAnnotation?.note.interpretation === 'cancel_line'
                  ? 'Accept and cancel'
                  : 'Accept'}
              </AlertDialogAction>
            </AlertDialogFooter>
          </AlertDialogContent>
        </AlertDialog>

        <AlertDialog
          open={Boolean(rejectingAnnotation)}
          onOpenChange={(next) => !next && setRejectingAnnotation(null)}
        >
          <AlertDialogContent className="max-h-[85vh] overflow-y-auto">
            <AlertDialogHeader>
              <AlertDialogTitle>Reject this note?</AlertDialogTitle>
              <AlertDialogDescription>
                Nothing is applied. The note stays on the record as rejected, with your
                reason.
              </AlertDialogDescription>
            </AlertDialogHeader>
            <div className="space-y-1.5">
              <label className="text-xs font-medium" htmlFor="po-line-annot-reject-note">
                Reason
              </label>
              <Textarea
                id="po-line-annot-reject-note"
                rows={3}
                value={rejectNote}
                onChange={(event) => setRejectNote(event.target.value)}
                placeholder="Why this does not change the PO"
              />
            </div>
            <AlertDialogFooter>
              <AlertDialogCancel>Keep it open</AlertDialogCancel>
              <AlertDialogAction
                className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
                disabled={!rejectNote.trim()}
                onClick={() => {
                  if (rejectingAnnotation) {
                    lastActionedAnnotationId.current = rejectingAnnotation.note.id;
                    lastActionedLineId.current = rejectingAnnotation.lineId;
                    void onRejectAnnotation(rejectingAnnotation.note.id, rejectNote.trim());
                  }
                  setRejectingAnnotation(null);
                }}
              >
                Reject the note
              </AlertDialogAction>
            </AlertDialogFooter>
          </AlertDialogContent>
        </AlertDialog>

        {editingAnnotation && (
          <POIntakeAnnotationEditDialog
            annotation={editingAnnotation}
            saving={savingAnnotationIds.includes(editingAnnotation.id)}
            onClose={() => setEditingAnnotation(null)}
            onSubmit={async (body) => {
              const note = editingAnnotation;
              await onEditAnnotation(note.id, body);
              setEditingAnnotation(null);
              // An edit resolves the note the same way an accept does: whichever line's
              // panel opened this dialog, that is where focus moves on from.
              const fromLine = lines.find((line) =>
                (annotationsByLineNo.get(line.line_no) ?? []).some(
                  (candidate) => candidate.id === note.id,
                ),
              );
              lastActionedAnnotationId.current = note.id;
              lastActionedLineId.current = fromLine?.id ?? null;
            }}
          />
        )}

        {focusedLineId && (
          <p className="sr-only" aria-live="polite">
            {`Line ${lines.find((line) => line.id === focusedLineId)?.line_no ?? ''} in focus`}
          </p>
        )}
      </div>
    );
  },
);

function describeLine(line: POVersionLine): string {
  const parts = [
    `Line ${line.line_no}`,
    line.stock_code_raw ?? undefined,
    `${formatQty(line.qty)} ${line.uom_raw ?? ''}`.trim(),
    formatMyrExact(line.amount),
  ].filter(Boolean);
  return parts.join(', ');
}

/**
 * The line's handwritten notes, collapsed to one compact indicator (owner hand test 25 Sep
 * 2026, item 2): the stacked note cards that used to sit under every flagged row read as
 * noise ("looks messy and bulky, like so many expanded sections") once more than one or two
 * lines carried a note. One icon-and-count button per row instead, opening a popover with the
 * same three actions the card offered - row height stays the grid's ordinary one line whether
 * a line has zero notes or five.
 */
function LineNotesIndicator({
  line,
  notes,
  readOnly,
  open,
  onOpenChange,
  savingAnnotationIds,
  onShowPage,
  onAccept,
  onEdit,
  onReject,
}: {
  line: POVersionLine;
  /** Already filtered to what this reader should see: every note for a viewer, only the
   *  unreviewed ones for an editor (a resolved note has nothing left for them to do). */
  notes: POAnnotation[];
  readOnly: boolean;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  savingAnnotationIds: string[];
  onShowPage: (page: number) => void;
  onAccept: (note: POAnnotation) => void;
  onEdit: (note: POAnnotation) => void;
  onReject: (note: POAnnotation) => void;
}) {
  if (notes.length === 0) return null;

  const unreviewedCount = notes.filter((note) => note.state === 'proposed').length;
  const label = `${notes.length} note${notes.length === 1 ? '' : 's'}${
    unreviewedCount > 0 ? ' to review' : ''
  } on line ${line.line_no}`;

  return (
    <Popover open={open} onOpenChange={onOpenChange}>
      {/* `cellWrap` (the caller) puts an onClick on the whole cell that focuses the line -
          harmless on its own, but this trigger and everything in the portalled content below
          are still part of the SAME REACT tree as that div (a portal only moves where a node
          paints, not where its events bubble), so a plain click would also fire that outer
          handler. That collided with the popover's own Page-N button: clicking it called
          onShowPage(note's page) AND, via the bubble, onFocusLine(line) -> setPage(the LINE's
          own page), and the second call always won. Stopped at the source instead of chasing
          it through every action inside. */}
      <PopoverTrigger asChild onClick={(event) => event.stopPropagation()}>
        <Button
          type="button"
          variant="outline"
          size="sm"
          className={`h-6 gap-1 px-1.5 text-[11px] ${
            unreviewedCount > 0
              ? 'border-amber-500/50 bg-amber-50 text-amber-800 hover:bg-amber-100 dark:bg-amber-950/30 dark:text-amber-300'
              : ''
          }`}
          aria-label={label}
          title={label}
        >
          <StickyNote className="size-3" aria-hidden />
          {notes.length}
        </Button>
      </PopoverTrigger>
      <PopoverPortal>
        <PopoverContent
          align="start"
          className="w-80 space-y-2"
          onClick={(event) => event.stopPropagation()}
        >
          {notes.map((note, index) => {
            const saving = savingAnnotationIds.includes(note.id);
            const noteLabel = notes.length > 1 ? `note ${index + 1} on` : 'the note on';
            return (
              <div
                key={note.id}
                className="space-y-1.5 rounded-md border border-border px-3 py-2 last:mb-0"
              >
                <div className="flex flex-wrap items-center gap-1.5">
                  <Badge
                    variant={note.state === 'proposed' ? 'warning' : 'secondary'}
                    className="text-[11px]"
                  >
                    {note.state === 'rejected'
                      ? `Rejected: ${annotationBadgeLabel(note)}`
                      : annotationBadgeLabel(note)}
                  </Badge>
                  {note.written_date && (
                    <span className="text-xs text-muted-foreground">{note.written_date}</span>
                  )}
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    className="h-6 px-2 text-[11px]"
                    onClick={() => onShowPage(note.page_no)}
                  >
                    {`Page ${note.page_no}`}
                  </Button>
                </div>
                <p className="text-sm break-words" title={note.raw_text ?? undefined}>
                  {note.raw_text || (
                    <span className="text-muted-foreground">Nothing legible was read</span>
                  )}
                </p>
                {!readOnly && note.state === 'proposed' && (
                  <div className="flex flex-wrap items-center gap-1 pt-0.5">
                    <Button
                      type="button"
                      size="sm"
                      disabled={saving}
                      aria-label={`Accept ${noteLabel} line ${line.line_no}`}
                      onClick={() => onAccept(note)}
                    >
                      {saving ? (
                        <Loader2 className="size-3.5 animate-spin" aria-hidden />
                      ) : (
                        <Check className="size-3.5" aria-hidden />
                      )}
                      Accept
                    </Button>
                    <Button
                      type="button"
                      size="sm"
                      variant="outline"
                      disabled={saving}
                      aria-label={`Edit ${noteLabel} line ${line.line_no}`}
                      onClick={() => onEdit(note)}
                    >
                      <Pencil className="size-3.5" aria-hidden />
                      Edit
                    </Button>
                    <Button
                      type="button"
                      size="sm"
                      variant="outline"
                      disabled={saving}
                      aria-label={`Reject ${noteLabel} line ${line.line_no}`}
                      onClick={() => onReject(note)}
                    >
                      <X className="size-3.5" aria-hidden />
                      Reject
                    </Button>
                  </div>
                )}
              </div>
            );
          })}
        </PopoverContent>
      </PopoverPortal>
    </Popover>
  );
}

/**
 * Cell-commit editing, deliberately NOT the staged `InlineLineTable` paradigm the
 * quotation editor uses: these rows are the extractor's reading of the customer's paper,
 * corrected one misread cell at a time against the scan beside the grid, and each
 * correction is a fact worth keeping the moment it is typed. The full argument is
 * ADR-0008; a third editing paradigm needs that ADR revisited first.
 */
function CellInput({
  line,
  field,
  value,
  numeric,
  invalid,
  readOnly,
  label,
  onCommit,
}: {
  line: POVersionLine;
  field: string;
  value: string;
  numeric?: boolean;
  invalid?: boolean;
  readOnly: boolean;
  label: string;
  onCommit: (value: string) => void;
}) {
  const inputRef = React.useRef<HTMLInputElement>(null);

  if (readOnly) {
    return (
      <span
        className={`block truncate text-sm ${numeric ? 'text-right tabular-nums' : ''} ${line.is_cancelled ? 'line-through' : ''}`}
        title={value}
      >
        {value || '-'}
      </span>
    );
  }

  return (
    <Input
      // Remount when the server's value changes, so an uncontrolled cell never shows a
      // stale reading after a card is applied or a save is refused.
      key={`${line.id}-${field}-${value}`}
      ref={inputRef}
      defaultValue={value}
      aria-label={label}
      title={value}
      inputMode={numeric ? 'decimal' : undefined}
      className={`h-8 ${numeric ? 'text-right tabular-nums' : ''} ${
        invalid ? 'border-destructive focus-visible:ring-destructive/30' : ''
      } ${line.is_cancelled ? 'line-through' : ''}`}
      onKeyDown={(event) => {
        if (event.key === 'Enter') {
          event.preventDefault();
          inputRef.current?.blur();
        }
        if (event.key === 'Escape') {
          if (inputRef.current) inputRef.current.value = value;
          inputRef.current?.blur();
        }
      }}
      onBlur={(event) => {
        const next = event.target.value.trim();
        if (next === value) return;
        if (numeric && !isDecimalString(next)) {
          toast.error(`${label} must be a number`);
          event.target.value = value;
          return;
        }
        onCommit(next);
      }}
    />
  );
}
