'use client';

import * as React from 'react';
import { useRouter } from 'next/navigation';
import {
  ColumnDef,
  PaginationState,
  getCoreRowModel,
  getPaginationRowModel,
  useReactTable,
} from '@tanstack/react-table';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardFooter, CardHeader, CardTable } from '@/components/ui/card';
import { DataGrid } from '@/components/ui/data-grid';
import { DataGridListToolbar } from '@/components/ui/data-grid-list-toolbar';
import { DataGridPagination } from '@/components/ui/data-grid-pagination';
import { DataGridTable } from '@/components/ui/data-grid-table';
import { Skeleton } from '@/components/ui/skeleton';
import BackToList from '@/components/common/BackToList';
import { ListSearchInput } from '@/components/common/ListSearchInput';
import { PageHeader } from '@/components/common/PageHeader';
import RecordNavigation from '@/components/common/RecordNavigation';
import { SearchableSelect } from '@/components/common/SearchableSelect';
import { isSearchInFlight, useDebouncedSearch } from '@/hooks/useDebouncedSearch';
import { useResetPageOnFilterChange } from '@/hooks/useResetPageOnFilterChange';
import { formatDateInMalaysia, formatDateTimeInMalaysia } from '@/lib/helpers';
import { cn } from '@/lib/utils';
import { useParkedSendActions } from '../../_shared/hooks/useParkedSendActions';
import {
  useExportSnapshot,
  useSnapshotQuery,
  useSnapshotRowsQuery,
} from '../hooks/useStockDebtSnapshots';
import {
  VALUE_CELL_CLASS,
  buildStockDebtColumns,
  signed,
  type ValueCellArgs,
} from '../lib/stockDebtColumns';
import type {
  Snapshot,
  SnapshotDecisionFilter,
  SnapshotRow,
} from '../types/stockDebtSnapshot.types';
import { SnapshotDecisionPopover } from './SnapshotDecisionPopover';
import { SnapshotDrillDialog } from './SnapshotDrillDialog';
import { StockDebtExportPopover } from './StockDebtExportPopover';

const LIST_PATH = '/project-sales/stock-debt/snapshots';

const BOOK_LABEL: Record<string, string> = { all: 'All', project: 'Project', retail: 'Retail' };

const DECISION_OPTIONS: Array<{ value: SnapshotDecisionFilter; label: string }> = [
  { value: 'decided', label: 'Decided' },
  { value: 'undecided', label: 'Undecided' },
  { value: 'reserve', label: 'Reserve' },
  { value: 'buy', label: 'Buy' },
  { value: 'ignore', label: 'Ignore' },
];

const dmy = (iso: string) => formatDateInMalaysia(`${iso}T00:00:00Z`);

/** The read-only facts under the header, from what the snapshot stored. */
function metaFacts(snapshot: Snapshot): string[] {
  const filters = snapshot.filters;
  const facts = [
    `Taken ${formatDateTimeInMalaysia(snapshot.created_at)}${
      snapshot.created_by_name ? ` by ${snapshot.created_by_name}` : ''
    }`,
    `Book ${BOOK_LABEL[filters.book ?? 'all'] ?? filters.book}`,
  ];
  const included = filters.location_codes ?? [];
  const excluded = filters.exclude_location_codes ?? [];
  facts.push(
    included.length
      ? included.join(', ')
      : excluded.length
        ? `All except ${excluded.join(', ')}`
        : 'All locations',
  );
  if (filters.date_from || filters.date_to) {
    facts.push(
      filters.date_from && filters.date_to
        ? `Delivery ${dmy(filters.date_from)} to ${dmy(filters.date_to)}`
        : filters.date_from
          ? `Delivery from ${dmy(filters.date_from)}`
          : `Delivery to ${dmy(filters.date_to as string)}`,
    );
  }
  if (filters.only_debt) facts.push('Only in debt');
  if (filters.show_aux) facts.push('Stock and PO columns');
  facts.push(`${snapshot.row_count.toLocaleString()} products`);
  facts.push(`${snapshot.decided_count.toLocaleString()} decided`);
  return facts;
}

/** Which drill is open: a bucket (`month`), or every bucket (null). */
interface OpenDrill {
  row: SnapshotRow;
  month: string | null;
  label: string;
}

/** AC-F3: the frozen board. The columns ARE the stored payload; nothing is recomputed. */
export function SnapshotClient({ snapshotId }: { snapshotId: string }) {
  const router = useRouter();
  const {
    value: search,
    setValue: setSearch,
    debouncedValue: debounced,
    isSettling,
  } = useDebouncedSearch();
  const [decision, setDecision] = React.useState<SnapshotDecisionFilter>('');
  const [pagination, setPagination] = React.useState<PaginationState>({
    pageIndex: 0,
    pageSize: 25,
  });
  const [openDrill, setOpenDrill] = React.useState<OpenDrill | null>(null);

  useResetPageOnFilterChange(setPagination, [debounced, decision]);

  const header = useSnapshotQuery(snapshotId);
  const rowsQuery = useSnapshotRowsQuery(snapshotId, {
    pageIndex: pagination.pageIndex,
    pageSize: pagination.pageSize,
    query: debounced,
    decision,
  });
  const exportMutation = useExportSnapshot(snapshotId);
  const parked = useParkedSendActions();

  const snapshot = header.data;
  const rows = React.useMemo(() => rowsQuery.data?.data ?? [], [rowsQuery.data]);
  const total = rowsQuery.data?.pagination.total ?? 0;
  const months = React.useMemo(() => snapshot?.months ?? [], [snapshot]);
  const tbaMonth = snapshot?.tba_month ?? null;
  const totals = snapshot?.totals;
  const showAux = Boolean(snapshot?.filters.show_aux);

  const columns = React.useMemo<ColumnDef<SnapshotRow>[]>(() => {
    const renderValueCell = ({
      row,
      month,
      label,
      balance,
      toneClass,
      openDrill: opens,
    }: ValueCellArgs<SnapshotRow>) => (
      <button
        type="button"
        onClick={() => opens && setOpenDrill({ row, month, label })}
        title={`${row.product_code} - ${label}: ${signed(balance)}`}
        aria-label={`${row.product_code}, ${label}, balance ${signed(balance)}`}
        className={cn(VALUE_CELL_CLASS, toneClass)}
      >
        {signed(balance)}
      </button>
    );
    return buildStockDebtColumns<SnapshotRow>({
      months,
      tbaMonth,
      totals,
      showAux,
      renderValueCell,
      renderColumnHeader: (_key, label, titleText) => (
        <span className="block truncate text-end" title={titleText}>
          {label}
        </span>
      ),
      renderProduct: (row, content) => (
        <button
          type="button"
          className="block w-full min-w-0 text-start hover:underline"
          onClick={() => setOpenDrill({ row, month: null, label: 'all months' })}
        >
          {content}
        </button>
      ),
      afterProduct: [
        {
          id: 'decision',
          header: 'Decision',
          meta: { headerTitle: 'Decision', skeleton: <Skeleton className="h-4 w-20" /> },
          size: 150,
          cell: ({ row }) => (
            <SnapshotDecisionPopover snapshotId={snapshotId} row={row.original} />
          ),
        },
      ],
    });
  }, [months, tbaMonth, totals, showAux, snapshotId]);

  const table = useReactTable({
    data: rows,
    columns,
    getRowId: (row) => row.row_id,
    initialState: { columnPinning: { left: ['product'] } },
    state: { pagination },
    onPaginationChange: setPagination,
    pageCount: Math.max(1, Math.ceil(total / pagination.pageSize)),
    manualPagination: true,
    enableSorting: false,
    getCoreRowModel: getCoreRowModel(),
    getPaginationRowModel: getPaginationRowModel(),
    columnResizeMode: 'onChange',
  });

  const filtered = Boolean(debounced || decision);
  const goTo = (id: string | null) => {
    if (id) router.push(`${LIST_PATH}/${id}`);
  };

  const title = (
    <span className="inline-flex min-w-0 items-center gap-2">
      <span className="truncate">{snapshot?.name ?? 'Snapshot'}</span>
      {snapshot && (
        <Badge variant="secondary" appearance="light">
          Snapshot
        </Badge>
      )}
    </span>
  );

  return (
    <div className="min-w-0 space-y-4">
      <PageHeader
        title={title}
        crumbTitle={snapshot?.name ?? 'Snapshot'}
        actions={
          <div className="flex items-center gap-2">
            {snapshot && (
              <RecordNavigation
                index={null}
                total={0}
                hasPrevious={Boolean(snapshot.prev_id)}
                hasNext={Boolean(snapshot.next_id)}
                onPrevious={() => goTo(snapshot.prev_id)}
                onNext={() => goTo(snapshot.next_id)}
                ariaLabel="snapshot"
              />
            )}
            <BackToList listPath={LIST_PATH} label="Back to snapshots" appendListState={false} />
          </div>
        }
      />

      {header.isError ? (
        <div className="rounded-lg border border-destructive/40 bg-destructive/5 px-6 py-10 text-center">
          <h2 className="text-sm font-semibold text-destructive">The snapshot could not be loaded</h2>
          <p className="mx-auto mt-1 max-w-md text-sm text-muted-foreground">
            {header.error instanceof Error ? header.error.message : 'Try again shortly.'}
          </p>
          <Button variant="outline" className="mt-4" onClick={() => void header.refetch()}>
            Retry
          </Button>
        </div>
      ) : (
        <>
          {snapshot && (
            <div className="flex flex-wrap gap-x-5 gap-y-1 text-sm text-muted-foreground">
              {metaFacts(snapshot).map((fact) => (
                <span key={fact}>{fact}</span>
              ))}
            </div>
          )}
          <DataGrid
            table={table}
            recordCount={total}
            isLoading={header.isLoading || rowsQuery.isLoading}
            isPlaceholderData={rowsQuery.isPlaceholderData}
            listingKey={null}
            tableLayout={{
              width: 'fixed',
              columnsResizable: true,
              columnsPinnable: true,
              columnsDraggable: false,
            }}
            emptyMessage={
              <div className="px-6 py-10 text-center">
                <p className="text-sm font-semibold">
                  {filtered ? 'No product matches' : 'No product in this snapshot'}
                </p>
                <p className="mx-auto mt-1 max-w-md text-sm text-muted-foreground">
                  {filtered ? 'Clear the search and the decision filter.' : 'Nothing was in debt when it was taken.'}
                </p>
              </div>
            }
          >
            <Card>
              <CardHeader className="block">
                <DataGridListToolbar
                  table={table}
                  showColumns={false}
                  exportConfig={false}
                  searchSlot={
                    <div className="flex w-full flex-wrap items-center gap-2">
                      <ListSearchInput
                        value={search}
                        onChange={setSearch}
                        isSettling={isSearchInFlight(isSettling, rowsQuery.isFetching, debounced)}
                        placeholder="Search product code or name…"
                        aria-label="Search products"
                        className="w-full max-w-xs"
                      />
                      <SearchableSelect
                        aria-label="Decision"
                        value={decision}
                        onChange={(value) => setDecision(value as SnapshotDecisionFilter)}
                        options={DECISION_OPTIONS}
                        placeholder="Decision"
                        triggerClassName="w-40"
                        clearable
                      />
                    </div>
                  }
                  secondaryActions={parked.actions}
                  secondaryActionsAsMenu
                  primaryAction={
                    <StockDebtExportPopover
                      rowCount={snapshot?.row_count ?? 0}
                      exporting={exportMutation.isPending}
                      onExport={(split, done) =>
                        exportMutation.mutate({ split, show_aux: showAux }, { onSuccess: done })
                      }
                    />
                  }
                />
              </CardHeader>
              <CardTable>
                {rowsQuery.isError ? (
                  <div className="rounded-lg border border-destructive/40 bg-destructive/5 px-6 py-10 text-center">
                    <h2 className="text-sm font-semibold text-destructive">
                      The snapshot rows could not be loaded
                    </h2>
                    <p className="mx-auto mt-1 max-w-md text-sm text-muted-foreground">
                      {rowsQuery.error instanceof Error ? rowsQuery.error.message : 'Try again shortly.'}
                    </p>
                    <Button variant="outline" className="mt-4" onClick={() => void rowsQuery.refetch()}>
                      Retry
                    </Button>
                  </div>
                ) : (
                  <div className="relative w-full overflow-x-auto overscroll-x-contain">
                    <DataGridTable />
                  </div>
                )}
              </CardTable>
              <CardFooter>
                <DataGridPagination />
              </CardFooter>
            </Card>
          </DataGrid>
        </>
      )}

      {parked.dialog}

      {openDrill && snapshot && (
        <SnapshotDrillDialog
          snapshotId={snapshotId}
          row={openDrill.row}
          month={openDrill.month === 'total' ? null : openDrill.month}
          label={openDrill.label}
          takenAt={snapshot.created_at}
          onClose={() => setOpenDrill(null)}
        />
      )}
    </div>
  );
}
