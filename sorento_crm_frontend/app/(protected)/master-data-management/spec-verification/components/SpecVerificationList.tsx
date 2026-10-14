'use client';

import { useEffect, useMemo, useRef, useState } from 'react';
import Link from 'next/link';
import { usePathname, useRouter, useSearchParams } from 'next/navigation';
import {
  ColumnDef,
  getCoreRowModel,
  getFilteredRowModel,
  getPaginationRowModel,
  OnChangeFn,
  PaginationState,
  RowSelectionState,
  SortingState,
  useReactTable,
} from '@tanstack/react-table';
import {
  BadgeCheck,
  BadgeX,
  ChevronRight,
  LoaderCircleIcon,
} from 'lucide-react';
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
import { Card, CardFooter, CardHeader, CardTable } from '@/components/ui/card';
import {
  HoverCard,
  HoverCardContent,
  HoverCardTrigger,
} from '@/components/ui/hover-card';
import { DataGrid } from '@/components/ui/data-grid';
import { DataGridColumnHeader } from '@/components/ui/data-grid-column-header';
import {
  DataGridListToolbar,
  type ToolbarAction,
} from '@/components/ui/data-grid-list-toolbar';
import { DataGridPagination } from '@/components/ui/data-grid-pagination';
import { buildSelectColumn } from '@/components/ui/data-grid-select-column';
import { DataGridTable } from '@/components/ui/data-grid-table';
import { Label } from '@/components/ui/label';
import { Skeleton } from '@/components/ui/skeleton';
import { Switch } from '@/components/ui/switch';
import { SearchableSelect } from '@/components/common/SearchableSelect';
import { usePermissions } from '@/hooks/usePermissions';
import { isSearchInFlight, useDebouncedSearch } from '@/hooks/useDebouncedSearch';
import { useDeferredBulkAction } from '@/hooks/useDeferredBulkAction';
import { useDeferredRowAction } from '@/hooks/useDeferredRowAction';
import { ListSearchInput } from '@/components/common/ListSearchInput';
import { formatDateTimeInMalaysia } from '@/lib/helpers';
import { pendingEntityKey, usePendingEntityKeys } from '@/lib/pending-entity-store';
import { readable, readableEntry, valueLabelsByKey } from '@/lib/spec-readable';
import { statusPillClass, STATUS_PILL_BASE } from '@/lib/status-pill';
import { useSpecRegistryQuery } from '../../product-specifications/hooks/useSpecRegistryQuery';
import {
  skippedVerifyCodes,
  useSpecVerificationMutations,
  useSpecVerificationWorklist,
  WORKLIST_KEY,
} from '../hooks/useSpecVerification';
import type {
  SpecVerificationCoverage,
  SpecVerificationRow,
  VerificationBlock,
  VerificationState,
} from '../types/specVerification.types';
import { distinctName } from '@/lib/product-display';

const DEFAULT_PAGE_SIZE = 25;

const STATE_LABEL: Record<VerificationState, string> = {
  verified: 'Verified',
  needs_reverify: 'Needs re-verify',
  unverified: 'Unverified',
};

const STATE_OPTIONS = [
  { value: 'needs_reverify', label: 'Needs re-verify' },
  { value: 'unverified', label: 'Unverified' },
  { value: 'verified', label: 'Verified' },
];

/** What a confirmation counts. "(s)" reads as copy nobody finished writing. */
function productCodeCount(n: number): string {
  return `${n} product code${n === 1 ? '' : 's'}`;
}

/** Who vouched for the code and when, or what moved under the stamp. */
function verificationTitle(
  block: VerificationBlock,
  valueLabels: Record<string, Record<string, string>>,
): string {
  const stamp =
    block.verified_by_name && block.verified_at
      ? `${block.verified_by_name} on ${formatDateTimeInMalaysia(block.verified_at)}`
      : null;
  if (block.state === 'verified') {
    return stamp ? `Verified by ${stamp}` : 'Verified';
  }
  if (block.state === 'needs_reverify') {
    const changed = block.invalidated_diff?.changed ?? [];
    // The diff carries the stored ENTRIES (`{ value, unit }`), not scalars - `readable`
    // is the fallback for a key with no label of its own (E.2).
    const diff = changed
      .map((c) => {
        const labels = valueLabels[c.spec_key];
        return `${readable(c.spec_key)}: ${readableEntry(c.was, labels) || 'nothing'} to ${
          readableEntry(c.now, labels) || 'nothing'
        }`;
      })
      .join('; ');
    const head = stamp ? `Was verified by ${stamp}.` : 'Was verified.';
    return changed.length
      ? `${head} ${changed.length} changed - ${diff}`
      : head;
  }
  if (block.invalidated_reason === 'manual_unverify') {
    const who = block.invalidated_by_name ?? 'someone';
    const when = block.invalidated_at
      ? formatDateTimeInMalaysia(block.invalidated_at)
      : '';
    const withdrawn = when
      ? `Withdrawn by ${who} on ${when}.`
      : `Withdrawn by ${who}.`;
    return stamp ? `${withdrawn} Originally verified by ${stamp}` : withdrawn;
  }
  return 'Not verified yet';
}

/**
 * The coverage figure, and the specs behind it.
 *
 * "3 / 8" says how much is known and nothing about WHICH, so judging a row meant
 * opening the product. What this code actually says is already on the row
 * (`coverage.items`), so the cell answers "show me what is set": the count line
 * carries the gap, the list carries only the keys that hold a value - a wall of
 * "not set" rows is the same information the count already gave. Hovering opens it,
 * tabbing to it opens it, and so does TAPPING it - the same `HoverCard` the reorder
 * grid uses for a cell that has more to say than it can show, held open here rather
 * than left uncontrolled because a touch device has no hover and would otherwise get
 * the count and nothing else.
 */
function CoverageCell({
  coverage,
  openExceptions,
}: {
  coverage: SpecVerificationCoverage;
  openExceptions: number;
}) {
  // A key "holds a value" only if it reads as something, so the filter runs on the
  // rendered text: an entry present but empty (`{ value: null }`) is not a value.
  const filled = (coverage.items ?? [])
    .map((item) => ({ ...item, text: readableEntry(item.value) }))
    .filter((item) => item.text);
  const [open, setOpen] = useState(false);
  const summary = `${coverage.have} of ${coverage.applicable} applicable keys hold a value`;
  // The payload carries only a count, never reasons (AC-F.4), so the pill and the
  // hover card both say the same one sentence.
  const exceptionsSummary =
    openExceptions > 0 ? `${openExceptions} need a human` : '';

  return (
    <HoverCard open={open} onOpenChange={setOpen} openDelay={120}>
      <HoverCardTrigger asChild>
        <div className="flex flex-wrap items-center gap-1.5">
          <button
            type="button"
            className="text-sm tabular-nums underline decoration-dotted underline-offset-4"
            // The row navigates on click; this cell answers in place instead. The toggle
            // is what makes it work under a finger: hover and focus still open it, and a
            // second tap closes it.
            onClick={(e) => {
              e.stopPropagation();
              setOpen((wasOpen) => !wasOpen);
            }}
            aria-expanded={open}
            aria-label={`Coverage: ${summary}`}
            // The last resort, for a pointer that reports neither hover nor a usable tap.
            title={summary}
          >
            {coverage.have} / {coverage.applicable}
          </button>
          {openExceptions > 0 && (
            <Badge
              variant="warning"
              appearance="light"
              shape="circle"
              size="sm"
              title={exceptionsSummary}
            >
              {exceptionsSummary}
            </Badge>
          )}
        </div>
      </HoverCardTrigger>
      <HoverCardContent className="w-72 p-3" align="start">
        <div className="text-xs uppercase tracking-wide text-muted-foreground">
          {summary}
        </div>
        {openExceptions > 0 && (
          <div className="mt-2 text-sm text-amber-800">{exceptionsSummary}</div>
        )}
        {filled.length === 0 ? (
          <p className="mt-2 text-sm text-muted-foreground">Nothing set yet</p>
        ) : (
          <ul className="mt-2 flex max-h-64 flex-col gap-1 overflow-y-auto text-sm">
            {filled.map((item) => (
              <li key={item.spec_key} className="break-words">
                <span className="font-medium">{item.label}</span>:{' '}
                <span>{item.text}</span>
              </li>
            ))}
          </ul>
        )}
      </HoverCardContent>
    </HoverCard>
  );
}

export default function SpecVerificationList() {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();

  // Filters live in the URL, so a link is a shareable slice and a refresh
  // resumes in place (AC-D.17b). Seeded once, written back on every change.
  const [pagination, setPagination] = useState<PaginationState>(() => {
    const page = parseInt(searchParams.get('page') ?? '1', 10);
    const limit = parseInt(
      searchParams.get('limit') ?? String(DEFAULT_PAGE_SIZE),
      10,
    );
    return {
      pageIndex: Number.isNaN(page) ? 0 : Math.max(0, page - 1),
      // This worklist's own cap, not the shared one: its rows are wide and its
      // backend route allows up to MAX_PAGE_LIMIT, so nothing forced it up to
      // 1000 and a hundred spec rows is already a long scroll.
      pageSize:
        Number.isNaN(limit) || limit < 1 ? DEFAULT_PAGE_SIZE : Math.min(limit, 100),
    };
  });
  const [sorting, setSorting] = useState<SortingState>(() => {
    const sort = searchParams.get('sort');
    return sort && sort !== 'default'
      ? [{ id: sort, desc: searchParams.get('dir') === 'desc' }]
      : [];
  });
  const {
    value: searchInputValue,
    setValue: setSearchInputValue,
    debouncedValue: searchQuery,
    isSettling: searchSettling,
    reset: resetSearchQuery,
  } = useDebouncedSearch(searchParams.get('query') ?? '');
  const [stateFilter, setStateFilter] = useState(
    () => searchParams.get('state') ?? '',
  );
  const [classFilter, setClassFilter] = useState(
    () => searchParams.get('class_label') ?? '',
  );
  const [includeDiscontinued, setIncludeDiscontinued] = useState(
    () => searchParams.get('include_discontinued') === 'true',
  );
  // Ticking rows and then opening one of them to check it is the journey; a selection
  // that did not survive that trip made the reviewer re-tick the page (captain ruling
  // 2026-08-17). Page-scoped as before: dropped whenever the page changes.
  const [rowSelection, setRowSelection] = useState<RowSelectionState>(() => {
    const selected = searchParams.get('selected');
    if (!selected) return {};
    return Object.fromEntries(
      selected
        .split(',')
        .filter(Boolean)
        .map((code) => [code, true]),
    );
  });
  /**
   * The row to bring back into view, read ONCE at first render.
   *
   * The URL-writing effect below never emits `focus`, so its first pass strips the
   * param - which is what makes the restore idempotent, the same trick
   * `GuideTargetSpotlight` uses.
   */
  const [focusCode] = useState(() => searchParams.get('focus'));
  const focusDone = useRef(false);
  // Verify is not destructive, but a BULK verify still confirms first (unchanged);
  // this carries the codes the dialog was opened for. Unverify (row and bulk) no
  // longer confirms - it is a deferred action instead (D7, D8, AC-F.1).
  const [verifyConfirm, setVerifyConfirm] = useState<string[] | null>(null);

  // Deps are primitives, and an unchanged URL is not rewritten: `sorting` /
  // `pagination` are fresh objects on some table renders, and replacing the URL
  // with itself remounts this subtree, which restarts the query forever.
  const sortField = sorting[0]?.id ?? '';
  const sortDesc = sorting[0]?.desc ?? false;
  const { pageIndex, pageSize } = pagination;
  // The raw map, not the table's selected row model: it is what was seeded from the
  // URL, and it is populated before the rows have loaded.
  const selectedParam = Object.keys(rowSelection)
    .filter((code) => rowSelection[code])
    .join(',');
  useEffect(() => {
    const next = new URLSearchParams();
    if (searchQuery) next.set('query', searchQuery);
    if (stateFilter) next.set('state', stateFilter);
    if (classFilter) next.set('class_label', classFilter);
    if (includeDiscontinued) next.set('include_discontinued', 'true');
    if (sortField) {
      next.set('sort', sortField);
      next.set('dir', sortDesc ? 'desc' : 'asc');
    }
    if (pageIndex > 0) next.set('page', String(pageIndex + 1));
    if (pageSize !== DEFAULT_PAGE_SIZE) next.set('limit', String(pageSize));
    if (selectedParam) next.set('selected', selectedParam);
    const qs = next.toString();
    const target = qs ? `${pathname}?${qs}` : pathname;
    if (`${window.location.pathname}${window.location.search}` === target)
      return;
    router.replace(target, { scroll: false });
  }, [
    router,
    pathname,
    searchQuery,
    stateFilter,
    classFilter,
    includeDiscontinued,
    sortField,
    sortDesc,
    pageIndex,
    pageSize,
    selectedParam,
  ]);

  const { data, isLoading, isPlaceholderData, isError, error, refetch, isFetching } =
    useSpecVerificationWorklist({
      pageIndex: pagination.pageIndex,
      pageSize: pagination.pageSize,
      sorting,
      searchQuery,
      state: (stateFilter || '') as VerificationState | '',
      class_label: classFilter,
      include_discontinued: includeDiscontinued,
    });
  const { verify } = useSpecVerificationMutations();
  const pending = verify.isPending;
  // The server is the guard; this only decides what to SHOW - the same slug and the
  // same rule the Specifications tab uses, so a reader is not offered a button that
  // would 403 at submit.
  const { permissionSet } = usePermissions();
  const canEdit = permissionSet.has('master_data.products.edit');
  // `{spec_key: value_labels}` (E.2) - the registry is small and ETag-cached (D9),
  // so this list pays no extra round trip for it.
  const { data: registryKeys } = useSpecRegistryQuery();
  const valueLabels = useMemo(() => valueLabelsByKey(registryKeys), [registryKeys]);

  // Every row parks its OWN "spec_verification.unverify" action (dimmed here by the
  // same store a single-row and a bulk countdown both write to), so the grid can
  // tell which rows are mid-window without a second query.
  const pendingKeys = usePendingEntityKeys();
  const rowUnverifyPending = (row: SpecVerificationRow) =>
    pendingKeys.has(pendingEntityKey('spec_verification', row.product_code));

  // Row Unverify asks nothing (D7, D8): the first press IS the action, and the
  // countdown travels to a toast while the row dims - a row has nowhere to put an
  // inline one, and the same reason every other list-row delete in the S6b sweep
  // takes this hook rather than `useDeferredAction` directly.
  const unverifyRow = useDeferredRowAction({
    actionKey: 'spec_verification.unverify',
    entityType: 'spec_verification',
    verb: 'Unverifying',
    successMessage: 'Unverified',
    invalidateKeys: [[WORKLIST_KEY]],
  });

  // "Unverify selected" is the same shape over a SELECTION: one action per row,
  // ONE countdown over them, one Cancel that withdraws the lot - the aggregate
  // `useDeferredBulkAction` every other bulk delete/withdraw in the S6b sweep uses.
  const bulkUnverify = useDeferredBulkAction({
    actionKey: 'spec_verification.unverify',
    entityType: 'spec_verification',
    verb: 'Unverifying',
    pastVerb: 'unverified',
    describe: (count) => productCodeCount(count),
    invalidateKeys: [[WORKLIST_KEY]],
    onStarted: () => setRowSelection({}),
  });

  const rows = useMemo(() => data?.data ?? [], [data]);
  const summary = data?.summary;
  // The class facet rides the worklist response, so no second call is made for it.
  // Held in a ref across refetches so the dropdown does not blink empty while the page
  // it just filtered is still loading.
  const classOptionsRef = useRef<string[]>([]);
  if (data?.classes?.length) classOptionsRef.current = data.classes;
  const classOptions = classOptionsRef.current;
  const filtersActive = Boolean(
    searchQuery || stateFilter || classFilter || includeDiscontinued,
  );

  const clearFilters = () => {
    resetSearchQuery('');
    setStateFilter('');
    setClassFilter('');
    setIncludeDiscontinued(false);
    setPagination((prev) => ({ ...prev, pageIndex: 0 }));
  };

  /**
   * Anything skipped stays selected so it can be dealt with; everything acted on
   * is released (AC-D.11). A row button never selects a row that was not already
   * selected - it acts on one code, it does not start a selection.
   */
  const settleSelection = (codes: string[], skipped: string[]) => {
    setRowSelection((prev) => {
      const next: RowSelectionState = { ...prev };
      for (const code of codes) {
        if (skipped.includes(code) && prev[code]) next[code] = true;
        else delete next[code];
      }
      return next;
    });
  };

  const runVerify = async (codes: string[]) => {
    const items = rows
      .filter((row) => codes.includes(row.product_code))
      .map((row) => ({
        product_code: row.product_code,
        values_hash: row.values_hash,
      }));
    if (!items.length) return;
    let response;
    try {
      response = await verify.mutateAsync(items);
    } catch {
      return;
    }
    settleSelection(codes, skippedVerifyCodes(response.results));
  };

  const columns = useMemo<ColumnDef<SpecVerificationRow>[]>(
    () => [
      buildSelectColumn<SpecVerificationRow>({ size: 44 }),
      {
        accessorKey: 'product_code',
        id: 'code',
        header: ({ column }) => (
          <DataGridColumnHeader title="Code" column={column} />
        ),
        cell: ({ row }) => (
          <span
            className="font-medium text-sm truncate block"
            title={row.original.product_code}
            data-spec-code={row.original.product_code}
          >
            {row.original.product_code}
          </span>
        ),
        size: 120,
        meta: {
          headerTitle: 'Code',
          skeleton: <Skeleton className="h-4 w-24" />,
        },
        enableSorting: true,
        enableHiding: false,
      },
      {
        accessorKey: 'product_name',
        id: 'name',
        header: ({ column }) => (
          <DataGridColumnHeader title="Name" column={column} />
        ),
        cell: ({ row }) => (
          <span
            className="text-sm truncate block"
            title={distinctName(row.original.product_code, row.original.product_name) ?? undefined}
          >
            {distinctName(row.original.product_code, row.original.product_name)}
          </span>
        ),
        size: 165,
        meta: {
          headerTitle: 'Name',
          skeleton: <Skeleton className="h-4 w-48" />,
        },
        enableSorting: false,
      },
      {
        accessorKey: 'class_label',
        id: 'class',
        header: ({ column }) => (
          <DataGridColumnHeader title="Class" column={column} />
        ),
        cell: ({ row }) => (
          <span
            className="text-sm truncate block"
            title={row.original.class_label ?? 'Not classified'}
          >
            {row.original.class_label ?? 'Not classified'}
          </span>
        ),
        size: 95,
        meta: {
          headerTitle: 'Class',
          skeleton: <Skeleton className="h-4 w-20" />,
        },
        enableSorting: false,
      },
      {
        accessorKey: 'brand_name',
        id: 'brand',
        header: ({ column }) => (
          <DataGridColumnHeader title="Brand" column={column} />
        ),
        cell: ({ row }) => (
          <span
            className="text-sm truncate block"
            title={row.original.brand_name ?? '-'}
          >
            {row.original.brand_name ?? '-'}
          </span>
        ),
        size: 90,
        meta: {
          headerTitle: 'Brand',
          skeleton: <Skeleton className="h-4 w-20" />,
        },
        enableSorting: false,
      },
      {
        accessorKey: 'coverage',
        id: 'coverage',
        header: ({ column }) => (
          <DataGridColumnHeader title="Coverage" column={column} />
        ),
        cell: ({ row }) => (
          <CoverageCell
            coverage={row.original.coverage}
            openExceptions={row.original.open_exceptions}
          />
        ),
        // Wide enough for "n / N" beside the "n need a human" pill without the two
        // overlapping (F5); the cell also drops the shared `truncate` (which forces
        // `white-space: nowrap` and hid the overlap rather than avoiding it) so the
        // pill wraps to its own line if a row's numbers still run long.
        size: 150,
        meta: {
          headerTitle: 'Coverage',
          skeleton: <Skeleton className="h-4 w-12" />,
          cellClassName: 'whitespace-normal! overflow-visible!',
        },
        enableSorting: true,
      },
      {
        accessorKey: 'verification',
        id: 'verification',
        header: ({ column }) => (
          <DataGridColumnHeader title="Verification" column={column} />
        ),
        cell: ({ row }) => {
          const block = row.original.verification;
          return (
            <span
              className={`${STATUS_PILL_BASE} ${statusPillClass(block.state)}`}
              title={verificationTitle(block, valueLabels)}
            >
              {STATE_LABEL[block.state]}
            </span>
          );
        },
        size: 125,
        meta: {
          headerTitle: 'Verification',
          skeleton: <Skeleton className="h-5 w-20" />,
        },
        enableSorting: false,
      },
      {
        id: 'actions',
        header: '',
        cell: ({ row }) => {
          const item = row.original;
          const isVerified = item.verification.state === 'verified';
          return (
            <div
              className="flex items-center justify-end gap-1"
              onClick={(e) => e.stopPropagation()}
            >
              {!canEdit ? null : isVerified ? (
                <Button
                  size="sm"
                  variant="outline"
                  disabled={
                    unverifyRow.isPending &&
                    unverifyRow.targetId === item.product_code
                  }
                  onClick={() =>
                    unverifyRow.run({
                      id: item.product_code,
                      subject: item.product_code,
                    })
                  }
                >
                  Unverify
                </Button>
              ) : (
                <Button
                  size="sm"
                  variant="outline"
                  disabled={pending}
                  onClick={() => void runVerify([item.product_code])}
                >
                  Verify
                </Button>
              )}
              <ChevronRight className="text-muted-foreground/70 size-3.5" />
            </div>
          );
        },
        size: 130,
        meta: {
          headerTitle: 'Actions',
          skeleton: <Skeleton className="h-7 w-20" />,
        },
        enableSorting: false,
        enableHiding: false,
        enableResizing: false,
      },
    ],
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [pending, rows, canEdit, valueLabels, unverifyRow],
  );

  const [columnOrder, setColumnOrder] = useState<string[]>(() =>
    columns.map((column) => column.id as string),
  );

  const handleSortingChange: OnChangeFn<SortingState> = (updater) => {
    setSorting(updater);
    // Page 4 of the old order is a different set of rows in the new one; the filters
    // already go back to the first page for the same reason.
    setPagination((prev) =>
      prev.pageIndex === 0 ? prev : { ...prev, pageIndex: 0 },
    );
  };

  const table = useReactTable({
    columns,
    data: rows,
    pageCount: Math.ceil((data?.pagination.total ?? 0) / pagination.pageSize),
    getRowId: (row: SpecVerificationRow) => row.product_code,
    state: { pagination, sorting, columnOrder, rowSelection },
    columnResizeMode: 'onChange',
    enableRowSelection: true,
    onRowSelectionChange: setRowSelection,
    onColumnOrderChange: setColumnOrder,
    onPaginationChange: setPagination,
    onSortingChange: handleSortingChange,
    getCoreRowModel: getCoreRowModel(),
    getFilteredRowModel: getFilteredRowModel(),
    getPaginationRowModel: getPaginationRowModel(),
    manualPagination: true,
    manualSorting: true,
    manualFiltering: true,
  });

  // Selection is page-scoped (AC-D.10), so it is dropped when the VISIBLE SET changes:
  // a code the user can no longer see must not be carried into a bulk action. That is
  // the page, and equally a filter - searching or switching state kept a tick on a row
  // that had scrolled out of existence, and the toolbar then offered to verify it.
  // Resetting empties `rowSelection`, so the effect above sheds `selected` from the URL
  // in the same pass. Guarded on the key actually moving rather than left to run on
  // mount too, which would wipe the selection the URL just restored.
  const visibleSetKey = `${pageIndex}:${pageSize}:${searchQuery}:${stateFilter}:${classFilter}:${includeDiscontinued}`;
  const pageKey = useRef(visibleSetKey);
  useEffect(() => {
    if (pageKey.current === visibleSetKey) return;
    pageKey.current = visibleSetKey;
    table.resetRowSelection();
  }, [table, visibleSetKey]);

  // Put the reviewer back on the row they left from. Once, and only when the rows the
  // page was resumed with are on screen.
  // NOT FOUND IS NOT DONE. Rows can be in state a render before the grid has painted
  // them: `DataGrid` shows skeletons until the column preferences answer, and that
  // render happens inside the grid, so there is no state HERE to key an effect on.
  // Marking the attempt spent on that pass burnt the single shot the restore gets and
  // left the reviewer at the top of the list instead of on the row they came back to.
  // So the shot is only spent on a real node, and until then it retries for a second -
  // the same "the target mounts later" problem `GuideTargetSpotlight` solves, at the
  // small end of it.
  useEffect(() => {
    if (!focusCode || focusDone.current) return;
    let attempts = 0;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const bringIntoView = () => {
      const target = Array.from(
        document.querySelectorAll('[data-spec-code]'),
      ).find((el) => el.getAttribute('data-spec-code') === focusCode);
      if (target) {
        focusDone.current = true;
        target.scrollIntoView?.({ block: 'center' });
        return;
      }
      attempts += 1;
      if (attempts < 10) timer = setTimeout(bringIntoView, 100);
    };
    bringIntoView();
    return () => clearTimeout(timer);
  }, [focusCode, rows]);

  // Read off the table's own selected rows rather than the raw selection map. The map
  // can still hold a code from a page the user has left, which would make the
  // toolbar's count disagree with what a bulk action actually sends. Not memoised:
  // the row model is memoised inside the table, and this is only read in a click
  // handler.
  const selectedCodes = table
    .getSelectedRowModel()
    .rows.map((selected) => selected.original.product_code);

  const confirmVerify = async () => {
    const codes = verifyConfirm;
    setVerifyConfirm(null);
    if (codes) await runVerify(codes);
  };

  // A reader is offered no bulk action; the toolbar then shows only the selection
  // count and Clear, which is what an empty list means to it.
  const bulkActions: ToolbarAction[] = canEdit
    ? [
        {
          key: 'verify',
          label: 'Verify selected',
          icon: BadgeCheck,
          disabled: pending,
          onClick: () => setVerifyConfirm(selectedCodes),
        },
        {
          key: 'unverify',
          label: 'Unverify selected',
          icon: BadgeX,
          disabled: bulkUnverify.isStarting,
          onClick: () =>
            bulkUnverify.run(selectedCodes.map((code) => ({ id: code }))),
        },
      ]
    : [];

  /**
   * Open the product, carrying this exact list back with it.
   *
   * `back` is the whole worklist URL - search, filters, sort, page and selection, which
   * the effect above keeps current - plus `focus`, the row being left. The detail
   * page's Back link returns to it, so the round trip costs the reviewer nothing
   * (captain ruling 2026-08-17). Read off the location rather than rebuilt from state,
   * so there is one spelling of that URL.
   */
  const openProduct = (row: SpecVerificationRow) => {
    const params = new URLSearchParams(window.location.search);
    params.set('focus', row.product_code);
    const back = `${pathname}?${params.toString()}`;
    router.push(
      `/master-data-management/products/${row.product_id}?tab=specifications&back=${encodeURIComponent(back)}`,
    );
  };

  // Every other filter on this list resets the page from its own onChange; the
  // search box settles on its own schedule (the debounce), so it gets the same
  // reset from an effect instead.
  useEffect(() => {
    setPagination((prev) => ({ ...prev, pageIndex: 0 }));
  }, [searchQuery]);

  if (isError) {
    return (
      <Card>
        <CardTable>
          <div className="p-8 text-center text-muted-foreground">
            <p className="font-medium text-destructive">
              Failed to load the verification worklist.
            </p>
            <p className="text-sm mt-1">
              {error instanceof Error ? error.message : 'Please try again.'}
            </p>
            <Button
              variant="outline"
              size="sm"
              className="mt-4"
              onClick={() => void refetch()}
            >
              Retry
            </Button>
          </div>
        </CardTable>
      </Card>
    );
  }

  const confirmCount = verifyConfirm?.length ?? 0;
  const confirmCopy = {
    title: 'Confirm verify',
    description: `Verify ${productCodeCount(confirmCount)}? A code whose values moved while you were reviewing is reported back as skipped.`,
    actionLabel: 'Verify',
  };

  return (
    <>
      <DataGrid
        table={table}
        recordCount={data?.pagination.total ?? 0}
        isLoading={isLoading}
        isPlaceholderData={isPlaceholderData}
        onRowClick={(row) => openProduct(row)}
        rowPending={rowUnverifyPending}
        listingKey="master_data.products.view::spec-verification"
        tableLayout={{
          width: 'fixed',
          columnsResizable: true,
          columnsVisibility: true,
        }}
        tableClassNames={{ edgeCell: 'px-5' }}
        emptyMessage={
          <div className="py-6 text-center">
            <p className="font-medium">Nothing to review here.</p>
            <p className="text-sm text-muted-foreground mt-1">
              {filtersActive
                ? 'No product code matches these filters.'
                : 'No product code is waiting for verification.'}
            </p>
            {filtersActive ? (
              <Button
                variant="outline"
                size="sm"
                className="mt-4"
                onClick={clearFilters}
              >
                Clear filters
              </Button>
            ) : (
              <Button variant="outline" size="sm" className="mt-4" asChild>
                <Link href="/master-data-management/products">
                  Go to products
                </Link>
              </Button>
            )}
          </div>
        }
      >
        <Card>
          <CardHeader className="block space-y-3">
            <div
              className="text-sm text-muted-foreground"
              data-testid="verification-progress"
            >
              {summary ? (
                <>
                  Verified {summary.verified.toLocaleString()} of{' '}
                  {summary.total.toLocaleString()}{' '}
                  {includeDiscontinued ? 'codes' : 'live codes'}
                </>
              ) : (
                <Skeleton className="h-4 w-56" />
              )}
            </div>
            <DataGridListToolbar
              table={table}
              searchSlot={
                <ListSearchInput
                  value={searchInputValue}
                  onChange={setSearchInputValue}
                  isSettling={isSearchInFlight(searchSettling, isFetching, searchQuery)}
                  placeholder="Search code or name"
                  className="w-full sm:w-40 md:w-64"
                />
              }
              filters={{
                kind: 'custom',
                active: Boolean(
                  stateFilter || classFilter || includeDiscontinued,
                ),
                activeCount:
                  (stateFilter ? 1 : 0) +
                  (classFilter ? 1 : 0) +
                  (includeDiscontinued ? 1 : 0),
                content: (
                  <div className="space-y-3">
                    <div className="space-y-1.5">
                      <Label htmlFor="spec-verification-state">
                        Verification
                      </Label>
                      <SearchableSelect
                        id="spec-verification-state"
                        value={stateFilter}
                        clearable
                        placeholder="Any state"
                        options={STATE_OPTIONS}
                        onChange={(value) => {
                          setStateFilter(value);
                          setPagination((prev) => ({ ...prev, pageIndex: 0 }));
                        }}
                      />
                    </div>
                    <div className="space-y-1.5">
                      <Label htmlFor="spec-verification-class">Class</Label>
                      <SearchableSelect
                        id="spec-verification-class"
                        value={classFilter}
                        clearable
                        placeholder="Any class"
                        options={classOptions.map((label) => ({
                          value: label,
                          label,
                        }))}
                        onChange={(value) => {
                          setClassFilter(value);
                          setPagination((prev) => ({ ...prev, pageIndex: 0 }));
                        }}
                      />
                    </div>
                    <div className="flex items-center justify-between gap-3">
                      <Label htmlFor="spec-verification-discontinued">
                        Include discontinued
                      </Label>
                      <Switch
                        id="spec-verification-discontinued"
                        checked={includeDiscontinued}
                        onCheckedChange={(checked) => {
                          setIncludeDiscontinued(checked);
                          setPagination((prev) => ({ ...prev, pageIndex: 0 }));
                        }}
                      />
                    </div>
                    <Button
                      variant="outline"
                      size="sm"
                      className="w-full"
                      onClick={clearFilters}
                    >
                      Clear filters
                    </Button>
                  </div>
                ),
              }}
              exportConfig={false}
              onRefresh={() => void refetch()}
              isRefreshing={isFetching}
              bulkActions={bulkActions}
            />
          </CardHeader>
          <CardTable>
            <DataGridTable />
          </CardTable>
          <CardFooter>
            <DataGridPagination />
          </CardFooter>
        </Card>
      </DataGrid>

      <AlertDialog
        open={verifyConfirm !== null}
        onOpenChange={(open) => {
          if (!open) setVerifyConfirm(null);
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{confirmCopy.title}</AlertDialogTitle>
            <AlertDialogDescription>
              {confirmCopy.description}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={pending}>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={(e) => {
                e.preventDefault();
                void confirmVerify();
              }}
              disabled={pending}
            >
              {pending ? (
                <>
                  <LoaderCircleIcon className="size-4 animate-spin" />
                  {confirmCopy.actionLabel}...
                </>
              ) : (
                confirmCopy.actionLabel
              )}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}
