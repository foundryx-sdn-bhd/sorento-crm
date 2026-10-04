'use client';

import * as React from 'react';
import Link from 'next/link';
import { useQueryClient } from '@tanstack/react-query';
import { getCoreRowModel, useReactTable, type ColumnDef } from '@tanstack/react-table';
import { CheckCircle2, Languages } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card, CardHeader, CardHeading, CardTable, CardTitle } from '@/components/ui/card';
import { DataGrid } from '@/components/ui/data-grid';
import { DataGridTable } from '@/components/ui/data-grid-table';
import { ListSearchInput } from '@/components/common/ListSearchInput';
import { SearchableSelect } from '@/components/common/SearchableSelect';
import type { SearchableSelectOption } from '@/components/common/SearchableSelect';
import {
  aliasTargetFor,
  fetchProductOrSetOptions,
  renderProductOrSetOption,
} from '../../components/productOrSetPicker';
import { useDeferredRowAction, useRowPending } from '@/hooks/useDeferredRowAction';
import {
  useConfirmSupplierCodeDecisions,
  useDismissSupplierCodeInPlace,
  useMatchSupplierCodeInPlace,
  useSupplierCodeAliases,
  useUndoSupplierCodeDecision,
  useUnmatchedSupplierCodes,
} from '../../hooks/useSupplierCodeAliases';
import type {
  SupplierCodeAlias,
  UnmatchedSupplierCode,
} from '../../services/supplierCodeAliasService';
import { RefreshMatchingButton } from './RefreshMatchingButton';
import type { PlanDocumentKind } from '../../services/fulfilmentService';
import { EM_DASH, fmtInt } from '../../lib/format';
import { formatDateInMalaysia, formatDateTimeInMalaysia } from '@/lib/helpers';

/**
 * The Supplier codes tab (S3): what this supplier's file names that our catalogue does not
 * (R16, R17), and what this supplier has ever been ruled on (R16's memory, now visible - the
 * captain's "I need this in UI" markup, 2 Sep).
 *
 * Two groups, always both on screen:
 *
 * - "Needs a decision": today's queue. Picking or dismissing a code keeps the ROW where it
 *   is (AC-C1/AC-C2) - the mutation runs, but the unmatched query is never invalidated on
 *   pick or dismiss, so nothing else on the row moves. A local `decided` map remembers what
 *   was just chosen and swaps the picker for the answer plus an Undo link; Undo runs the
 *   same DELETE `Forget` uses, immediately (no countdown - undoing a pick made seconds ago
 *   is a correction, not a destructive act on someone else's data), and that IS when the
 *   query is invalidated. A decided row otherwise waits for the next load - leaving the tab
 *   (unmount) invalidates the same query - or for "Confirm (N)" beside Refresh matching
 *   (S10 fix 2): N = rows decided this visit, disabled at 0, and the click writes nothing
 *   (the aliases already exist) - it only asks the queue and the memory to refetch, so the
 *   decided rows move into Remembered without waiting for a reload (AC-C3).
 * - "Remembered": every ruling this supplier has ever had, matched and dismissed alike -
 *   the memory R16 built and nowhere showed. Forget is the existing deferred row action
 *   (5s, reversible), unchanged from the old dismissed-only list.
 */

/** What a ruling points AT, as the owner reads it: the code, and nothing else (R5). The
 *  product's own name is a second column's worth of text in a cell she scans for a code. */
function matchedToLabel(alias: SupplierCodeAlias): string {
  if (alias.source === 'dismissed') return 'Dismissed';
  if (alias.set_code) return alias.set_code;
  if (alias.product_code) return alias.product_code;
  return EM_DASH;
}

/** What the queue row says in words, which is what a search over it reads. Leads with the
 *  supplier's own \u578b\u53f7 (owner feedback round 5) - `item_code` can be OUR composed guess for
 *  a bare model, and "Supplier says" has to say what the sheet actually printed. Omitted
 *  when it is identical to the row's own code (a letter-led model, or a proforma line,
 *  which names no separate \u578b\u53f7 at all) - repeating the code back at itself says nothing. */
function saysText(row: UnmatchedSupplierCode): string {
  const modelNo = row.model_no && row.model_no !== row.item_code ? row.model_no : null;
  return [modelNo, row.product_name, row.brand, row.spec].filter(Boolean).join(' \u00b7 ');
}

/** Whitespace-split, lower-cased, empties dropped - every token has to hit something. */
function searchTokens(query: string): string[] {
  return query.toLowerCase().split(/\s+/).filter(Boolean);
}

function matchesTokens(tokens: string[], haystacks: (string | null | undefined)[]): boolean {
  const fields = haystacks.filter(Boolean).map((h) => (h as string).toLowerCase());
  return tokens.every((token) => fields.some((field) => field.includes(token)));
}

/** What was just decided on a "Needs a decision" row, kept only for this visit (AC-C3). */
type RowDecision =
  | { kind: 'matched'; aliasId: string; label: string }
  | { kind: 'dismissed'; aliasId: string };

/**
 * Which statement these codes came off, in a sentence (AC-G2).
 *
 * Composed here rather than printed from `document_label` verbatim because the label is
 * written to head a record ("Proforma invoice X · 5 blocks") and this line is written to
 * answer "where am I being asked about these codes from". A plan with no file says so
 * outright: it reads no statement, so its queue is empty by design rather than by accident.
 */
function statementLine(
  documentKind: PlanDocumentKind,
  documentLabel: string,
  statementAsOf: string | null,
): string {
  if (documentKind === 'none') return 'No file on this plan';
  if (documentKind === 'stock_list') {
    return statementAsOf
      ? `Codes from the stock list of ${formatDateInMalaysia(statementAsOf)}`
      : `Codes from ${documentLabel}`;
  }
  // "Proforma invoice 2026-7-31 SORENTO 预装清单 · 5 blocks" reads as the file and its block
  // count once the record's own heading word is taken off the front. A plan whose invoice
  // carries no number at all is labelled a bare "Proforma invoice", and there is nothing to
  // take off it, so the line says which invoice it means in words instead.
  const named = documentLabel.replace(/^Proforma invoice\s*/, '').replace(' · ', ', ');
  return named ? `Codes from ${named}` : "Codes from this plan's proforma invoice";
}

export function SupplierCodesTab({
  planId,
  supplierId,
  documentKind,
  documentLabel,
  statementAsOf,
}: {
  /** The queue and Refresh matching are scoped to THIS plan's own rows (S6, AC-C7). */
  planId: string;
  /** Still the supplier's: a ruling is written against the supplier and remembered for
   *  every later upload, whichever plan answered it. */
  supplierId: string;
  documentKind: PlanDocumentKind;
  /** The plan's own statement, named (AC-G2) - never the supplier's latest. */
  documentLabel: string;
  statementAsOf: string | null;
}) {
  const {
    data: rows = [],
    error: unmatchedError,
    refetch: refetchUnmatched,
  } = useUnmatchedSupplierCodes(planId || null);
  const {
    data: aliases = [],
    error: aliasesError,
    refetch: refetchAliases,
  } = useSupplierCodeAliases(supplierId || null);
  const match = useMatchSupplierCodeInPlace();
  const dismiss = useDismissSupplierCodeInPlace();
  const undo = useUndoSupplierCodeDecision();
  const confirmDecisions = useConfirmSupplierCodeDecisions();
  // The same action the proforma detail and the old panel deferred, unchanged (D7): forgetting
  // a ruling un-binds every row it held, so it asks nothing up front and counts down instead.
  const forget = useDeferredRowAction({
    actionKey: 'supplier_code_alias.forget',
    entityType: 'supplier_code_alias',
    verb: 'Forgetting',
    successMessage: 'Match forgotten.',
    invalidateKeys: [
      ['scm', 'supplier-code-aliases'],
      ['scm', 'proforma-invoices'],
      ['scm', 'fulfilment'],
    ],
  });
  const rowPending = useRowPending<SupplierCodeAlias>('supplier_code_alias');
  const queryClient = useQueryClient();

  /** Picked or dismissed THIS visit, keyed by supplier code - AC-C1/AC-C2/AC-C3. */
  const [decided, setDecided] = React.useState<Record<string, RowDecision>>({});
  /** What is typed in the box above both tables (S1). Both lists are already in the browser,
   *  so the filter is a plain pass over them: no debounce, no query, no round trip. */
  const [search, setSearch] = React.useState('');
  /** The code a write is in flight for, so only ITS row goes quiet. */
  const [busy, setBusy] = React.useState<string | null>(null);

  // "Leaving the tab" - AC-C3's other trigger for the fresh truth to come back. Radix
  // unmounts an inactive TabsContent by default, so this fires on every tab switch and on
  // navigating off the plan; the deliberate case (Undo) invalidates on its own success too.
  // Only fires when something was actually decided this visit - nothing to catch up on
  // otherwise, and an unconditional refetch on every tab switch would be wasted work.
  //
  // Counted against `rows`, not `Object.keys(decided).length`: Confirm (S10 fix 2) asks the
  // queue to refetch without clearing `decided` (clearing it eagerly would flash the picker
  // back on a row an instant before the refetch removes it). Once the refetch lands, a
  // decided code is no longer among `rows` at all, so counting `decided` entries THAT ARE
  // STILL a row here is what makes the count - and the badge, and Confirm's own label - drop
  // back down on its own rather than staying pinned at the number just confirmed.
  const decidedCount = rows.filter((r) => decided[r.item_code]).length;
  const hasDecisions = decidedCount > 0;
  React.useEffect(() => {
    return () => {
      if (!hasDecisions) return;
      void queryClient.invalidateQueries({ queryKey: ['scm', 'supplier-code-aliases'] });
    };
  }, [hasDecisions, queryClient]);

  /**
   * Products AND our product sets in one list (R20), SERVER-searched and paginated - the
   * same picker the proforma detail's Match dialog uses (`productOrSetPicker.tsx`).
   */
  const fetchProducts = React.useCallback(fetchProductOrSetOptions, []);

  const onPick = React.useCallback(
    async (code: string, value: string, option: SearchableSelectOption | null) => {
      if (!value) return;
      setBusy(code);
      try {
        const written = await match.mutateAsync({
          supplier_id: supplierId,
          supplier_code: code,
          ...aliasTargetFor(value),
        });
        setDecided((prev) => ({
          ...prev,
          [code]: {
            kind: 'matched',
            aliasId: written.id,
            label: option?.label ?? written.set_code ?? written.product_code ?? 'Matched',
          },
        }));
      } catch {
        // The hook toasts the refusal; the row stays a picker.
      } finally {
        setBusy(null);
      }
    },
    [match, supplierId],
  );

  const onDismiss = React.useCallback(
    async (code: string) => {
      setBusy(code);
      try {
        const written = await dismiss.mutateAsync({ supplier_id: supplierId, supplier_code: code });
        setDecided((prev) => ({ ...prev, [code]: { kind: 'dismissed', aliasId: written.id } }));
      } catch {
        // Toasted by the hook.
      } finally {
        setBusy(null);
      }
    },
    [dismiss, supplierId],
  );

  const onUndo = React.useCallback(
    (code: string) => {
      const decision = decided[code];
      if (!decision) return;
      // `busy` is the CODE a write is in flight for, so only its own Undo goes quiet: keyed
      // on the mutation's `isPending`, one Undo greyed every other row's out with it.
      setBusy(code);
      undo.mutate(decision.aliasId, {
        onSuccess: () =>
          setDecided((prev) => {
            const next = { ...prev };
            delete next[code];
            return next;
          }),
        onSettled: () => setBusy(null),
      });
    },
    [decided, undo],
  );

  const needsDecisionColumns = React.useMemo<ColumnDef<UnmatchedSupplierCode>[]>(
    () => [
      {
        id: 'code',
        accessorKey: 'item_code',
        header: 'Code',
        size: 170,
        cell: ({ row }) => (
          <span className="block truncate text-sm font-medium" title={row.original.item_code}>
            {row.original.item_code}
          </span>
        ),
      },
      {
        id: 'says',
        accessorKey: 'product_name',
        header: 'Supplier says',
        size: 220,
        cell: ({ row }) => {
          // What the person matching it actually recognises: the code means nothing on its
          // own, and "连体马桶, SORENTO" means everything.
          const said = saysText(row.original) || EM_DASH;
          return (
            <span className="block truncate text-sm text-muted-foreground" title={said}>
              {said}
            </span>
          );
        },
      },
      {
        id: 'packed',
        accessorKey: 'qty_packed',
        header: 'Packed',
        size: 100,
        meta: { headerClassName: 'text-end', cellClassName: 'text-end' },
        cell: ({ row }) => (
          <span className="tabular-nums">{fmtInt(row.original.qty_packed || 0)}</span>
        ),
      },
      {
        id: 'product',
        accessorKey: 'item_code',
        header: 'Product',
        size: 300,
        cell: ({ row }) => {
          const code = row.original.item_code;
          const decision = decided[code];
          if (decision) {
            return (
              <div className="flex min-w-0 items-center gap-2">
                <span
                  className="min-w-0 truncate text-sm"
                  title={decision.kind === 'dismissed' ? 'Dismissed' : decision.label}
                >
                  {decision.kind === 'dismissed' ? 'Dismissed' : decision.label}
                </span>
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  className="shrink-0"
                  disabled={busy === code}
                  onClick={() => onUndo(code)}
                >
                  Undo
                </Button>
              </div>
            );
          }
          return (
            <SearchableSelect
              id={`unmatched-product-${code}`}
              value=""
              onChange={() => {}}
              onOptionChange={(opt) => void onPick(code, opt?.value ?? '', opt)}
              fetchOptions={fetchProducts}
              renderOption={renderProductOrSetOption}
              paginated
              pageSize={50}
              size="sm"
              disabled={busy === code}
              placeholder="Search a product or set"
              triggerClassName="w-full"
              clearable
            />
          );
        },
      },
      {
        id: 'dismiss',
        accessorKey: 'as_of',
        header: 'Dismiss',
        size: 100,
        cell: ({ row }) => {
          const code = row.original.item_code;
          if (decided[code]) return <span className="text-sm text-muted-foreground">{EM_DASH}</span>;
          return (
            <Button
              type="button"
              variant="ghost"
              size="sm"
              className="text-muted-foreground"
              disabled={busy === code}
              onClick={() => void onDismiss(code)}
            >
              Dismiss
            </Button>
          );
        },
      },
    ],
    [busy, decided, fetchProducts, onDismiss, onPick, onUndo],
  );

  const rememberedColumns = React.useMemo<ColumnDef<SupplierCodeAlias>[]>(
    () => [
      {
        id: 'code',
        accessorKey: 'supplier_code',
        header: 'Code',
        size: 170,
        cell: ({ row }) => (
          <span className="block truncate text-sm font-medium" title={row.original.supplier_code}>
            {row.original.supplier_code}
          </span>
        ),
      },
      {
        id: 'matched_to',
        header: 'Matched to',
        size: 260,
        cell: ({ row }) => {
          const label = matchedToLabel(row.original);
          return (
            <span className="block truncate text-sm" title={label}>
              {label}
            </span>
          );
        },
      },
      {
        id: 'when',
        header: 'When',
        size: 180,
        cell: ({ row }) => {
          // The record's own clock (`PRINCIPLES.md`): stored naive UTC, rendered as Malaysia
          // wall-clock. `fmtDateTime` reads a zone-less string as LOCAL, so this column sat
          // eight hours behind the "Started" line on the same screen. A row can carry no
          // `created_at` at all (ADR 1e): unknown is EM_DASH, never a formatter crash.
          const when = row.original.created_at
            ? formatDateTimeInMalaysia(row.original.created_at)
            : EM_DASH;
          return (
            <span className="block truncate text-sm text-muted-foreground" title={when}>
              {when}
            </span>
          );
        },
      },
      {
        id: 'by',
        header: 'By',
        size: 150,
        cell: ({ row }) => {
          const by = row.original.created_by || EM_DASH;
          return (
            <span className="block truncate text-sm text-muted-foreground" title={by}>
              {by}
            </span>
          );
        },
      },
      {
        id: 'forget',
        header: 'Forget',
        size: 100,
        cell: ({ row }) => (
          <Button
            type="button"
            variant="ghost"
            size="sm"
            className="text-muted-foreground"
            onClick={() =>
              forget.run({ id: row.original.id, subject: row.original.supplier_code })
            }
          >
            Forget
          </Button>
        ),
      },
    ],
    [forget],
  );

  // Newest ruling first, straight off the response: the backend orders by `created_at desc`
  // (AC-C5), so there is nothing left for this screen to re-sort.
  const remembered = aliases;

  // What the box narrows each table to (S1). Every token has to hit something, so
  // `CWC 250` finds the row both words describe rather than everything either of them does.
  // `rows` and `remembered` themselves stay whole: Confirm counts decisions, not what is on
  // screen, and a filter must not change what confirming writes.
  const filtering = searchTokens(search).length > 0;
  const visibleRows = React.useMemo(() => {
    const tokens = searchTokens(search);
    if (tokens.length === 0) return rows;
    return rows.filter((r) => matchesTokens(tokens, [r.item_code, saysText(r)]));
  }, [rows, search]);
  const visibleRemembered = React.useMemo(() => {
    const tokens = searchTokens(search);
    if (tokens.length === 0) return remembered;
    return remembered.filter((a) =>
      matchesTokens(tokens, [a.supplier_code, a.product_code, a.set_code]),
    );
  }, [remembered, search]);

  const needsTable = useReactTable({
    columns: needsDecisionColumns,
    data: visibleRows,
    getRowId: (row) => row.item_code,
    getCoreRowModel: getCoreRowModel(),
    columnResizeMode: 'onChange',
    enableColumnResizing: true,
  });

  const rememberedTable = useReactTable({
    columns: rememberedColumns,
    data: visibleRemembered,
    getRowId: (row) => row.id,
    getCoreRowModel: getCoreRowModel(),
    columnResizeMode: 'onChange',
    enableColumnResizing: true,
  });

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-xs text-muted-foreground" data-testid="supplier-codes-statement">
          {statementLine(documentKind, documentLabel, statementAsOf)}
        </p>
        {/* One box over both tables (S1): the queue and the memory answer the same question
            about the same code, and two boxes would be two places to type it. */}
        <div className="flex w-full flex-wrap items-center gap-2 sm:w-auto">
          <ListSearchInput
            value={search}
            onChange={setSearch}
            placeholder="Search code or supplier description"
            aria-label="Search supplier codes"
            className="w-full md:w-72"
            data-testid="supplier-codes-search"
          />
          {/* S4 (`PLAN-stock-list-bare-model-codes.md`) - the word list itself is edited on
              the Import field aliases page, not here; this is a link out, pinned to the
              word doc type so the tab it opens on is the one this button names. */}
          <Button variant="outline" size="sm" asChild>
            <Link href="/system-management/import-field-aliases?doc_type=supplier_inventory_word">
              <Languages className="size-4" />
              Stock list words
            </Link>
          </Button>
        </div>
      </div>

      <Card>
        <CardHeader className="flex flex-row items-center justify-between gap-3 py-3">
          <CardHeading className="min-w-0">
            <CardTitle className="truncate text-sm">
              {filtering
                ? `Needs a decision (${visibleRows.length} of ${rows.length})`
                : `Needs a decision (${rows.length})`}
            </CardTitle>
          </CardHeading>
          <div className="flex shrink-0 items-center gap-2">
            <Button
              type="button"
              size="sm"
              data-testid="confirm-decisions"
              disabled={!hasDecisions}
              onClick={confirmDecisions}
              title="Move what was decided this visit into Remembered - nothing new is written"
            >
              <CheckCircle2 className="size-4" />
              {`Confirm (${decidedCount})`}
            </Button>
            <RefreshMatchingButton planId={planId} size="sm" />
          </div>
        </CardHeader>
        {rows.length === 0 ? (
          <CardTable>
            <div className="flex flex-col items-center gap-3 p-10 text-center">
              <span className="flex size-10 items-center justify-center rounded-full bg-muted text-muted-foreground">
                <CheckCircle2 className="size-5" />
              </span>
              <p className="text-sm font-medium">Every code on file is matched</p>
            </div>
          </CardTable>
        ) : visibleRows.length === 0 ? (
          <CardTable>
            <div className="flex flex-col items-center gap-3 p-10 text-center">
              <p className="text-sm font-medium">No code matches</p>
            </div>
          </CardTable>
        ) : (
          <DataGrid
            table={needsTable}
            recordCount={visibleRows.length}
            error={unmatchedError}
            onRetry={() => void refetchUnmatched()}
            // Column personalisation OFF, as the old panel did: unset, the grid keys saved
            // widths on the URL, which here carries a supplier id.
            listingKey=""
            tableLayout={{ width: 'fixed', columnsResizable: true }}
          >
            <CardTable>
              <DataGridTable />
            </CardTable>
          </DataGrid>
        )}
      </Card>

      <Card>
        <CardHeader className="flex flex-row items-center justify-between gap-3 py-3">
          <CardHeading className="min-w-0">
            <CardTitle className="truncate text-sm">
              {filtering
                ? `Remembered (${visibleRemembered.length} of ${remembered.length})`
                : `Remembered (${remembered.length})`}
            </CardTitle>
          </CardHeading>
        </CardHeader>
        {remembered.length === 0 ? (
          <CardTable>
            <div className="flex flex-col items-center gap-3 p-10 text-center">
              <p className="text-sm font-medium">Nothing remembered for this supplier yet</p>
            </div>
          </CardTable>
        ) : visibleRemembered.length === 0 ? (
          <CardTable>
            <div className="flex flex-col items-center gap-3 p-10 text-center">
              <p className="text-sm font-medium">No code matches</p>
            </div>
          </CardTable>
        ) : (
          <DataGrid
            table={rememberedTable}
            recordCount={visibleRemembered.length}
            error={aliasesError}
            onRetry={() => void refetchAliases()}
            listingKey=""
            tableLayout={{ width: 'fixed', columnsResizable: true }}
            rowPending={rowPending}
          >
            <CardTable>
              <DataGridTable />
            </CardTable>
          </DataGrid>
        )}
      </Card>
    </div>
  );
}

export default SupplierCodesTab;
