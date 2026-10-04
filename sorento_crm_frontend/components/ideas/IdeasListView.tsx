'use client';

/**
 * The Ideas list. Mirrors the ss Ideas list (ss main, `service_frontend/app/(protected)/ideation/
 * ideas/use-ideas-list-config.tsx`, `select-idea-rows.ts`, `ideas-view.tsx`): every idea loads once
 * and search, status, Active | Archived, filters, sort and paging all happen here, the actions and
 * their visibility rules are ss's, and the row "..." menu carries the same actions as the bulk one.
 * Built on the CRM list pieces (`DataGridListToolbar`, `buildSelectColumn`, `BulkActionsMenu`).
 */
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react';
import Link from 'next/link';
import {
  ColumnDef,
  getCoreRowModel,
  getPaginationRowModel,
  useReactTable,
  type PaginationState,
  type RowSelectionState,
  type SortingState,
} from '@tanstack/react-table';
import { Columns3, Download, Filter, Info, Plus } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardFooter, CardHeader, CardTable } from '@/components/ui/card';
import { DataGrid } from '@/components/ui/data-grid';
import { DataGridColumnHeader } from '@/components/ui/data-grid-column-header';
import { DataGridColumnVisibility } from '@/components/ui/data-grid-column-visibility';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { DataGridListToolbar } from '@/components/ui/data-grid-list-toolbar';
import { DataGridPagination } from '@/components/ui/data-grid-pagination';
import { buildSelectColumn } from '@/components/ui/data-grid-select-column';
import { DataGridTable } from '@/components/ui/data-grid-table';
import {
  HoverCard,
  HoverCardContent,
  HoverCardTrigger,
} from '@/components/ui/hover-card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Skeleton } from '@/components/ui/skeleton';
import { ToggleGroup, ToggleGroupItem } from '@/components/ui/toggle-group';
import { Container } from '@/components/common/container';
import LoadErrorState from '@/components/common/LoadErrorState';
import { ListSearchInput } from '@/components/common/ListSearchInput';
import { PageHeader } from '@/components/common/PageHeader';
import { RowActionsMenu } from '@/components/common/RowActionsMenu';
import { SearchableSelect } from '@/components/common/SearchableSelect';
import { BulkActionsMenu } from '@/app/(protected)/scm/components/BulkActionsMenu';
import {
  isSearchInFlight,
  useDebouncedSearch,
} from '@/hooks/useDebouncedSearch';
import { useRowPending } from '@/hooks/useDeferredRowAction';
import { useDeferredBulkAction } from '@/hooks/useDeferredBulkAction';
import { useIdeaBulk } from '@/hooks/useIdeaBulk';
import { IDEAS_KEY, useIdeaMutations, useIdeasQuery } from '@/hooks/useIdeas';
import { formatDate } from '@/lib/helpers';
import type { Idea } from '@/types/ideas';
import { IdeaCaptureModal } from './IdeaCaptureModal';
import { IdeasBulkMergeDialog } from './IdeasBulkMergeDialog';
import { IdeaStatusBadge } from './IdeaStatusBadge';
import { IdeasViewToggle } from './IdeasViewToggle';
import { VoteBox } from './VoteBox';
import {
  buildIdeaActions,
  buildIdeaRowActions,
  type IdeaActionHandlers,
} from './ideaBulkActions';
import { IDEA_SOURCE_LABEL } from './ideaLabels';
import { IDEAS_MANAGE_PERMISSION, IDEAS_VIEW_PERMISSION } from './ideasAccess';
import { selectIdeaRows } from './selectIdeaRows';
import { useHasPermission } from '@/hooks/usePermissions';

const describeIdeas = (n: number) => `${n} idea${n === 1 ? '' : 's'}`;
const PAGE_SIZES = [10, 25, 50, 100];
const CHANNEL_OPTIONS = Object.entries(IDEA_SOURCE_LABEL).map(
  ([value, label]) => ({ value, label }),
);
const uniqueOptions = (values: string[]) =>
  [...new Set(values)]
    .sort((a, b) => a.localeCompare(b))
    .map((v) => ({ value: v, label: v }));

type View = 'active' | 'archived';

export function IdeasListView({
  toolbarScopeSlot,
}: {
  toolbarScopeSlot?: ReactNode;
}) {
  const canManage = useHasPermission(IDEAS_MANAGE_PERMISSION);
  const [captureOpen, setCaptureOpen] = useState(false);
  const [view, setView] = useState<View>('active');
  const [status, setStatus] = useState('');
  const [submitter, setSubmitter] = useState('');
  const [channel, setChannel] = useState('');
  const [from, setFrom] = useState('');
  const [to, setTo] = useState('');
  const [sorting, setSorting] = useState<SortingState>([]);
  const [pagination, setPagination] = useState<PaginationState>({
    pageIndex: 0,
    pageSize: PAGE_SIZES[0],
  });
  const [rowSelection, setRowSelection] = useState<RowSelectionState>({});
  const [mergeRows, setMergeRows] = useState<Idea[] | null>(null);

  const {
    value: searchQuery,
    setValue: setSearchQuery,
    debouncedValue: debouncedSearch,
    isSettling,
  } = useDebouncedSearch();
  const {
    data,
    isLoading,
    isFetching,
    isPlaceholderData,
    isError,
    error,
    refetch,
  } = useIdeasQuery({ status: 'all' });
  const { vote } = useIdeaMutations();
  // `mutate` is stable across renders; the mutation object is not, and columns that depend on it
  // are rebuilt (and every cell remounted) each time a request changes state.
  const castVote = vote.mutate;
  const ideas = useMemo<Idea[]>(() => data ?? [], [data]);

  const rows = useMemo(
    () =>
      selectIdeaRows(ideas, {
        search: debouncedSearch,
        view,
        status,
        submitter,
        channel,
        from,
        to,
        sort: sorting[0] ? { id: sorting[0].id, desc: sorting[0].desc } : null,
      }),
    [
      ideas,
      debouncedSearch,
      view,
      status,
      submitter,
      channel,
      from,
      to,
      sorting,
    ],
  );

  // A new result set starts on its first page.
  useEffect(() => {
    setPagination((p) => (p.pageIndex === 0 ? p : { ...p, pageIndex: 0 }));
  }, [debouncedSearch, view, status, submitter, channel, from, to, sorting]);

  const clearSelection = useCallback(() => setRowSelection({}), []);
  const changeView = (next: string) => {
    if (next !== 'active' && next !== 'archived') return;
    setView(next);
    clearSelection();
  };

  // The selection in the order the rows were ticked: "first selected" decides a promoted BR's title.
  const selected = useMemo(() => {
    const byId = new Map(ideas.map((i) => [i.id, i]));
    return Object.keys(rowSelection)
      .filter((id) => rowSelection[id])
      .map((id) => byId.get(id))
      .filter((i): i is Idea => !!i);
  }, [ideas, rowSelection]);

  const bulk = useIdeaBulk(clearSelection);
  const archiveAction = useDeferredBulkAction({
    actionKey: 'idea.archive',
    entityType: 'idea',
    verb: 'Archiving',
    pastVerb: 'archived',
    describe: describeIdeas,
    invalidateKeys: [IDEAS_KEY],
    onStarted: clearSelection,
  });
  const deleteAction = useDeferredBulkAction({
    actionKey: 'idea.delete',
    entityType: 'idea',
    verb: 'Deleting',
    pastVerb: 'deleted',
    describe: describeIdeas,
    invalidateKeys: [IDEAS_KEY],
    onStarted: clearSelection,
  });
  const rowPending = useRowPending<Idea>('idea');

  // The handlers close over hook state that changes every render; the columns must not, or every
  // cell and header remounts (a header clicked twice would be a stale node the second time). So
  // the columns see one stable object that always calls the latest handlers.
  const latest = useRef<IdeaActionHandlers | null>(null);
  latest.current = {
    promote: (r) => void bulk.promote(r),
    merge: (r) => setMergeRows(r),
    unmerge: (r) => void bulk.unmerge(r),
    advance: (r) => void bulk.advance(r),
    restore: (r) => void bulk.restore(r),
    archive: (r) => archiveAction.run(r.map((i) => ({ id: i.id }))),
    remove: (r) => deleteAction.run(r.map((i) => ({ id: i.id }))),
  };
  const handlers = useMemo<IdeaActionHandlers>(
    () => ({
      promote: (r) => latest.current?.promote(r),
      merge: (r) => latest.current?.merge(r),
      unmerge: (r) => latest.current?.unmerge(r),
      advance: (r) => latest.current?.advance(r),
      restore: (r) => latest.current?.restore(r),
      archive: (r) => latest.current?.archive(r),
      remove: (r) => latest.current?.remove(r),
    }),
    [],
  );

  const columns = useMemo<ColumnDef<Idea>[]>(() => {
    const col = (
      id: string,
      title: string,
      value: (idea: Idea) => string | number,
      cell: ColumnDef<Idea>['cell'],
      size: number,
    ): ColumnDef<Idea> => ({
      id,
      accessorFn: value,
      header: ({ column }) => (
        <DataGridColumnHeader title={title} column={column} />
      ),
      cell,
      size,
      enableSorting: true,
      meta: { headerTitle: title, skeleton: <Skeleton className="h-4 w-16" /> },
    });
    const text = (value: string) => (
      <span className="block truncate" title={value}>
        {value}
      </span>
    );
    return [
      buildSelectColumn<Idea>({ size: 44 }),
      {
        ...col(
          'votes',
          'Votes',
          (i) => i.upvotes,
          ({ row }) => (
            <VoteBox
              count={row.original.upvotes}
              voted={row.original.myVote === 'up'}
              disabled={!!row.original.mergedIntoId}
              onVote={() => castVote(row.original.id)}
            />
          ),
          64,
        ),
        enableResizing: false,
      },
      col(
        'idea',
        'Idea',
        (i) => i.title ?? i.problem,
        ({ row }) => {
          const label = row.original.title ?? row.original.problem;
          return (
            <div className="flex min-w-0 items-start gap-1.5 whitespace-normal">
              <Link
                href={`/ideas/${row.original.id}`}
                onClick={(e) => e.stopPropagation()}
                className="line-clamp-2 min-w-0 flex-1 break-words font-medium text-primary hover:underline"
                title={label}
              >
                {label}
              </Link>
              {row.original.mergedCount > 0 ? (
                <Badge variant="outline" size="sm" className="shrink-0">
                  {row.original.mergedCount} merged
                </Badge>
              ) : null}
              {row.original.mergedIntoId ? (
                <Badge variant="outline" size="sm" className="shrink-0">
                  Merged
                </Badge>
              ) : null}
            </div>
          );
        },
        164,
      ),
      col(
        'submitter',
        'Submitter',
        (i) => i.submitterName,
        ({ row }) => text(row.original.submitterName),
        128,
      ),
      col(
        'channel',
        'Channel',
        (i) => IDEA_SOURCE_LABEL[i.source] ?? i.source,
        ({ row }) => (
          <Badge variant="outline" size="sm">
            {IDEA_SOURCE_LABEL[row.original.source] ?? row.original.source}
          </Badge>
        ),
        120,
      ),
      col(
        'product',
        'Product',
        (i) => i.productName,
        ({ row }) => text(row.original.productName),
        124,
      ),
      col(
        'status',
        'Status',
        (i) => i.statusLabel,
        ({ row }) => (
          <IdeaStatusBadge
            label={row.original.statusLabel}
            color={row.original.statusColor}
          />
        ),
        124,
      ),
      col(
        'submitted',
        'Submitted',
        (i) => formatDate(i.createdAt),
        ({ row }) => (
          <span className="tabular-nums text-muted-foreground">
            {formatDate(row.original.createdAt)}
          </span>
        ),
        116,
      ),
      ...(canManage
        ? [
            {
              id: 'actions',
              header: '',
              cell: ({ row }) => (
                <RowActionsMenu
                  actions={buildIdeaRowActions(row.original, handlers)}
                  ariaLabel="Row"
                />
              ),
              size: 48,
              enableSorting: false,
              enableResizing: false,
              meta: { headerTitle: 'Actions' },
            } as ColumnDef<Idea>,
          ]
        : []),
    ];
  }, [castVote, canManage, handlers]);

  const table = useReactTable({
    columns,
    data: rows,
    getRowId: (row) => row.id,
    getCoreRowModel: getCoreRowModel(),
    getPaginationRowModel: getPaginationRowModel(),
    // Sorting and filtering are `selectIdeaRows`'; the table only reports what was asked.
    manualSorting: true,
    enableSortingRemoval: true,
    enableRowSelection: true,
    autoResetPageIndex: false,
    state: { sorting, pagination, rowSelection },
    onSortingChange: setSorting,
    onPaginationChange: setPagination,
    onRowSelectionChange: setRowSelection,
    columnResizeMode: 'onChange',
    enableColumnResizing: true,
  });

  const filtersActive = [submitter, channel, from || to].filter(Boolean).length;
  const anyFilter = Boolean(debouncedSearch || status || filtersActive);
  const clearFilters = () => {
    setSubmitter('');
    setChannel('');
    setFrom('');
    setTo('');
  };

  const statusOptions = useMemo(
    () => uniqueOptions(ideas.map((i) => i.statusLabel)),
    [ideas],
  );
  const submitterOptions = useMemo(
    () => uniqueOptions(ideas.map((i) => i.submitterName)),
    [ideas],
  );

  const emptyMessage = anyFilter ? (
    'No ideas match these filters.'
  ) : view === 'archived' ? (
    <div className="flex w-full flex-col items-center gap-1 py-6">
      <span className="text-sm font-medium">No archived ideas</span>
    </div>
  ) : (
    <div className="flex w-full flex-col items-center gap-1 py-6">
      <span className="text-sm font-medium">No ideas yet</span>
      <span className="text-sm text-muted-foreground">
        Captured ideas appear here.
      </span>
    </div>
  );

  const searchSlot = (
    <>
      <ListSearchInput
        value={searchQuery}
        onChange={setSearchQuery}
        isSettling={isSearchInFlight(isSettling, isFetching, debouncedSearch)}
        placeholder="Search ideas..."
        className="min-w-0 grow basis-40 sm:w-48 sm:grow-0 sm:basis-auto"
      />
      {/* While rows are selected the strip needs the room (one row at 1280): the info card and
          Status picker step aside, search and Active | Archived stay. */}
      {selected.length === 0 && (
        <>
          <HoverCard openDelay={0} closeDelay={0}>
            <HoverCardTrigger asChild>
              <Button
                type="button"
                variant="ghost"
                size="sm"
                mode="icon"
                className="shrink-0"
                aria-label="What can I search?"
              >
                <Info className="size-4" />
              </Button>
            </HoverCardTrigger>
            <HoverCardContent className="w-56 text-sm">
              <div className="flex flex-col gap-1">
                <p className="font-medium">You can search by</p>
                <ul className="flex flex-col gap-0.5 text-muted-foreground">
                  <li>Idea</li>
                  <li>Submitter</li>
                  <li>Product</li>
                </ul>
              </div>
            </HoverCardContent>
          </HoverCard>
          <div className="w-full sm:w-36">
            <SearchableSelect
              aria-label="Status"
              value={status}
              onChange={setStatus}
              options={statusOptions}
              placeholder="All statuses"
              emptyMessage="No statuses."
              clearable
            />
          </div>
        </>
      )}
      <ToggleGroup
        type="single"
        variant="outline"
        size="sm"
        value={view}
        aria-label="Show"
        onValueChange={changeView}
      >
        <ToggleGroupItem value="active" className="px-3">
          Active
        </ToggleGroupItem>
        <ToggleGroupItem value="archived" className="px-3">
          Archived
        </ToggleGroupItem>
      </ToggleGroup>
      {toolbarScopeSlot}
    </>
  );

  const filterContent = (
    <div className="space-y-3">
      <div className="space-y-1">
        <Label className="text-xs text-muted-foreground">Submitter</Label>
        <SearchableSelect
          aria-label="Submitter"
          value={submitter}
          onChange={setSubmitter}
          options={submitterOptions}
          placeholder="Any submitter"
          emptyMessage="No submitters."
          clearable
        />
      </div>
      <div className="space-y-1">
        <Label className="text-xs text-muted-foreground">Channel</Label>
        <SearchableSelect
          aria-label="Channel"
          value={channel}
          onChange={setChannel}
          options={CHANNEL_OPTIONS}
          placeholder="Any channel"
          emptyMessage="No channels."
          clearable
        />
      </div>
      <div className="grid grid-cols-2 gap-2">
        <div className="space-y-1">
          <Label
            htmlFor="ideas-filter-from"
            className="text-xs text-muted-foreground"
          >
            Submitted from
          </Label>
          <Input
            id="ideas-filter-from"
            type="date"
            value={from}
            max={to || undefined}
            onChange={(e) => setFrom(e.target.value)}
          />
        </div>
        <div className="space-y-1">
          <Label
            htmlFor="ideas-filter-to"
            className="text-xs text-muted-foreground"
          >
            Submitted to
          </Label>
          <Input
            id="ideas-filter-to"
            type="date"
            value={to}
            min={from || undefined}
            onChange={(e) => setTo(e.target.value)}
          />
        </div>
      </div>
      {filtersActive > 0 ? (
        <Button type="button" variant="ghost" size="sm" onClick={clearFilters}>
          Clear
        </Button>
      ) : null}
    </div>
  );

  return (
    <>
      <Container>
        <PageHeader title="Ideas" actions={<IdeasViewToggle active="list" />} />
      </Container>
      <Container>
        <div className="space-y-3">
          {isError && !data ? (
            <LoadErrorState
              className="rounded-lg border"
              title="Could not load ideas"
              message={error instanceof Error ? error.message : undefined}
              onRetry={() => void refetch()}
              retrying={isFetching}
            />
          ) : (
            <DataGrid
              table={table}
              recordCount={rows.length}
              isLoading={isLoading}
              isPlaceholderData={isPlaceholderData}
              listingKey={IDEAS_VIEW_PERMISSION}
              tableLayout={{ width: 'fixed', columnsResizable: true }}
              emptyMessage={emptyMessage}
              rowHref={(row) => `/ideas/${row.id}`}
              rowPending={rowPending}
            >
              <Card>
                <CardHeader className="block py-0">
                  <DataGridListToolbar
                    table={table}
                    searchSlot={searchSlot}
                    keepSearchWhileSelected
                    // The right cluster is built here, not by the toolbar's own buttons: it has to sit
                    // right-aligned in ONE row at 1280 (Columns and Export are icon buttons for that),
                    // and while rows are selected the strip carries Export, so the right cluster is empty.
                    alignActionsEnd
                    showColumns={false}
                    showExport={false}
                    exportConfig={{ filename: 'ideas.xlsx' }}
                    primaryAction={
                      <>
                        {selected.length === 0 ? (
                          <>
                            <DropdownMenu modal={false}>
                              <DropdownMenuTrigger asChild>
                                <Button
                                  variant="outline"
                                  size="sm"
                                  className="gap-1.5"
                                >
                                  <Filter className="size-4" />
                                  Filters
                                  {filtersActive > 0 ? (
                                    <Badge
                                      variant="secondary"
                                      className="ms-0.5 px-1 py-0 text-2xs"
                                    >
                                      {filtersActive}
                                    </Badge>
                                  ) : null}
                                </Button>
                              </DropdownMenuTrigger>
                              <DropdownMenuContent
                                align="end"
                                className="w-72 p-3"
                              >
                                {filterContent}
                              </DropdownMenuContent>
                            </DropdownMenu>
                            <DataGridColumnVisibility
                              table={table}
                              trigger={
                                <Button
                                  variant="outline"
                                  size="sm"
                                  mode="icon"
                                  aria-label="Columns"
                                  title="Columns"
                                >
                                  <Columns3 className="size-4" />
                                </Button>
                              }
                            />
                            <Button
                              variant="outline"
                              size="sm"
                              mode="icon"
                              aria-label="Export"
                              title="Select one or more rows to export"
                              disabled
                            >
                              <Download className="size-4" />
                            </Button>
                            <Button
                              variant="primary"
                              size="sm"
                              onClick={() => setCaptureOpen(true)}
                            >
                              <Plus className="size-4" />
                              Capture idea
                            </Button>
                          </>
                        ) : null}
                      </>
                    }
                    bulkActionsSlot={({ openExport }) => (
                      <>
                        {canManage ? (
                          <BulkActionsMenu
                            actions={buildIdeaActions(selected, handlers)}
                            modal={false}
                            onCloseAutoFocus={(event) => {
                              // The merge item opens a dialog: focus belongs to it, not back on the menu.
                              if (mergeRows) event.preventDefault();
                            }}
                          />
                        ) : null}
                        <Button
                          variant="outline"
                          size="sm"
                          className="gap-1.5"
                          onClick={openExport}
                        >
                          <Download className="size-4" />
                          Export
                        </Button>
                      </>
                    )}
                  />
                </CardHeader>
                <CardTable>
                  <DataGridTable />
                </CardTable>
                <CardFooter>
                  <DataGridPagination sizes={PAGE_SIZES} />
                </CardFooter>
              </Card>
            </DataGrid>
          )}
        </div>
      </Container>
      <IdeaCaptureModal open={captureOpen} onOpenChange={setCaptureOpen} />
      <IdeasBulkMergeDialog
        ideas={mergeRows ?? []}
        open={mergeRows !== null}
        onOpenChange={(open) => !open && setMergeRows(null)}
        onMerge={bulk.merge}
      />
    </>
  );
}
