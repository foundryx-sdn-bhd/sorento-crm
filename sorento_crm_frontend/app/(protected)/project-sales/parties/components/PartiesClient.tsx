'use client';

import * as React from 'react';
import { useRouter } from 'next/navigation';
import {
  ColumnDef,
  PaginationState,
  SortingState,
  getCoreRowModel,
  getPaginationRowModel,
  getSortedRowModel,
  useReactTable,
} from '@tanstack/react-table';
import { Pencil, Plus, Trash2 } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardFooter, CardHeader, CardTable } from '@/components/ui/card';
import { DataGrid } from '@/components/ui/data-grid';
import { DataGridColumnHeader } from '@/components/ui/data-grid-column-header';
import { DataGridListToolbar } from '@/components/ui/data-grid-list-toolbar';
import { DataGridPagination } from '@/components/ui/data-grid-pagination';
import { DataGridTable } from '@/components/ui/data-grid-table';
import { Label } from '@/components/ui/label';
import { Skeleton } from '@/components/ui/skeleton';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import { ConfirmDeleteDialog } from '@/components/common/ConfirmDeleteDialog';
import { SearchableSelect } from '@/components/common/SearchableSelect';
import { usePartyMutations, useProjectParties } from '../../_shared/hooks/useProjects';
import type { ProjectParty } from '../../_shared/types/project.types';
import { PartyFormDialog } from './PartyFormDialog';
import { PARTY_TYPE_OPTIONS, TYPE_LABEL } from './partyTypes';
import { PageHeader } from '@/components/common/PageHeader';
import { isSearchInFlight, useDebouncedSearch } from '@/hooks/useDebouncedSearch';
import { ListSearchInput } from '@/components/common/ListSearchInput';

/**
 * The organisation master, as the same list every other screen in the product uses.
 *
 * It was a wall of type-grouped cards. Grouping put the type in a heading and the
 * count in a badge, which reads well and sorts, filters and exports like nothing else
 * in the system. Type is a COLUMN now, and "which architects do we know" is the type
 * filter in the toolbar.
 *
 * One row per firm, reused across projects. That reuse is the entire value: "which
 * architects should we prioritise visiting" is only answerable when Veritas Architects
 * is one record rather than four spellings, which is why the project count is a column
 * and why same-name duplicates are refused rather than merged.
 *
 * Search and type are served by the SERVER (the same params the cards used). Paging is
 * client-side over the loaded set, because the endpoint is asked for the whole master
 * in one page and always was.
 */
export function PartiesClient() {
  const router = useRouter();
  const {
    value: search,
    setValue: setSearch,
    debouncedValue: debounced,
    isSettling: debouncedSettling,
  } = useDebouncedSearch();
  const [typeFilter, setTypeFilter] = React.useState('');
  const [editing, setEditing] = React.useState<ProjectParty | null>(null);
  const [creating, setCreating] = React.useState(false);
  const [deleting, setDeleting] = React.useState<ProjectParty | null>(null);
  const [pagination, setPagination] = React.useState<PaginationState>({
    pageIndex: 0,
    pageSize: 25,
  });
  const [sorting, setSorting] = React.useState<SortingState>([{ id: 'name', desc: false }]);

  const { remove } = usePartyMutations();

  // Narrowing the set changes which rows exist, so page 3 of the old set is a page of
  // nothing in the new one.
  React.useEffect(() => {
    setPagination((previous) => ({ ...previous, pageIndex: 0 }));
  }, [debounced, typeFilter]);

  const parties = useProjectParties({
    query: debounced || undefined,
    party_type: typeFilter || undefined,
    include_inactive: true,
    limit: 200,
  });

  const rows = React.useMemo(() => parties.data?.data ?? [], [parties.data]);
  const filtered = Boolean(debounced || typeFilter);

  const columns = React.useMemo<ColumnDef<ProjectParty>[]>(
    () => [
      {
        accessorKey: 'name',
        header: ({ column }) => <DataGridColumnHeader title="Name" column={column} />,
        size: 260,
        meta: { headerTitle: 'Name', skeleton: <Skeleton className="h-4 w-40" /> },
        cell: ({ row }) => (
          <div className="min-w-0">
            <span className="block truncate font-medium" title={row.original.name}>
              {row.original.name}
            </span>
            {row.original.registration_no && (
              <span
                className="block truncate text-xs text-muted-foreground"
                title={row.original.registration_no}
              >
                {row.original.registration_no}
              </span>
            )}
          </div>
        ),
      },
      {
        accessorKey: 'party_type',
        header: ({ column }) => <DataGridColumnHeader title="Type" column={column} />,
        size: 160,
        meta: { headerTitle: 'Type', skeleton: <Skeleton className="h-4 w-24" /> },
        cell: ({ row }) => {
          const label = TYPE_LABEL[row.original.party_type] ?? row.original.party_type;
          return (
            <Badge variant="secondary" className="truncate" title={label}>
              {label}
            </Badge>
          );
        },
      },
      {
        id: 'project_count',
        accessorFn: (row) => row.project_count ?? 0,
        header: ({ column }) => <DataGridColumnHeader title="Projects" column={column} />,
        size: 120,
        meta: { headerTitle: 'Projects', skeleton: <Skeleton className="h-4 w-14" /> },
        // The count is the reason the master exists: it is what turns "we know some
        // architects" into "these are the ones worth visiting".
        cell: ({ row }) => {
          const count = row.original.project_count ?? 0;
          return count > 0 ? (
            <span className="tabular-nums">
              {count} project{count === 1 ? '' : 's'}
            </span>
          ) : (
            <Muted>None yet</Muted>
          );
        },
      },
      {
        id: 'customer_name',
        accessorFn: (row) => row.customer_name ?? '',
        header: ({ column }) => <DataGridColumnHeader title="Buys as" column={column} />,
        size: 190,
        meta: { headerTitle: 'Buys as', skeleton: <Skeleton className="h-4 w-24" /> },
        cell: ({ row }) => (
          <span className="block truncate" title={row.original.customer_name ?? ''}>
            {row.original.customer_name ?? <Muted>Not a buyer</Muted>}
          </span>
        ),
      },
      {
        id: 'contact',
        accessorFn: (row) => [row.phone, row.email].filter(Boolean).join(' · '),
        header: ({ column }) => <DataGridColumnHeader title="Contact" column={column} />,
        size: 220,
        enableSorting: false,
        meta: { headerTitle: 'Contact', skeleton: <Skeleton className="h-4 w-28" /> },
        cell: ({ row }) => {
          const contact = [row.original.phone, row.original.email]
            .filter(Boolean)
            .join(' · ');
          return contact ? (
            <span className="block truncate" title={contact}>
              {contact}
            </span>
          ) : (
            <Muted>-</Muted>
          );
        },
      },
      {
        accessorKey: 'is_active',
        header: ({ column }) => <DataGridColumnHeader title="Status" column={column} />,
        size: 120,
        meta: { headerTitle: 'Status', skeleton: <Skeleton className="h-4 w-16" /> },
        cell: ({ row }) =>
          row.original.is_active ? (
            <Badge variant="outline">Active</Badge>
          ) : (
            <Badge variant="secondary">Inactive</Badge>
          ),
      },
      {
        id: 'actions',
        header: () => <span className="sr-only">Actions</span>,
        size: 110,
        enableSorting: false,
        enableHiding: false,
        cell: ({ row }) => (
          <div
            className="flex items-center gap-1"
            onClick={(event) => event.stopPropagation()}
          >
            <Tooltip>
              <TooltipTrigger asChild>
                <Button
                  variant="ghost"
                  size="icon"
                  className="h-8 w-8"
                  onClick={() => setEditing(row.original)}
                  aria-label={`Edit ${row.original.name}`}
                >
                  <Pencil className="size-4 text-muted-foreground" aria-hidden />
                </Button>
              </TooltipTrigger>
              <TooltipContent>Edit</TooltipContent>
            </Tooltip>
            <Tooltip>
              <TooltipTrigger asChild>
                <Button
                  variant="ghost"
                  size="icon"
                  className="h-8 w-8"
                  onClick={() => setDeleting(row.original)}
                  aria-label={`Delete ${row.original.name}`}
                >
                  <Trash2 className="size-4 text-destructive" aria-hidden />
                </Button>
              </TooltipTrigger>
              <TooltipContent>Delete</TooltipContent>
            </Tooltip>
          </div>
        ),
      },
    ],
    [],
  );

  const table = useReactTable({
    columns,
    data: rows,
    getRowId: (row) => row.id,
    state: { pagination, sorting },
    onPaginationChange: setPagination,
    onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
    getPaginationRowModel: getPaginationRowModel(),
    columnResizeMode: 'onChange',
    enableColumnResizing: true,
    defaultColumn: { minSize: 60, maxSize: 800, size: 150 },
  });

  return (
    <div className="space-y-5">
      <PageHeader
        title="Parties"
        actions={
          <Button type="button" onClick={() => setCreating(true)}>
            <Plus className="size-4" aria-hidden />
            Add party
          </Button>
        }
      />

      <DataGrid
        table={table}
        // A row IS the record, so clicking it opens the record.
        onRowClick={(row) => router.push(`/project-sales/parties/${row.id}`)}
        recordCount={rows.length}
        isLoading={parties.isLoading}
        error={parties.error}
        onRetry={() => void parties.refetch()}
        isPlaceholderData={parties.isPlaceholderData}
        // Pinned, never the pathname default: the fallback keys column preferences on
        // the current URL, so any route carrying an id would write one preferences row
        // per record. This is the parties listing, and it has exactly one key.
        listingKey="projects.parties.view"
        tableLayout={{ width: 'fixed', columnsResizable: true, columnsVisibility: true }}
        emptyMessage={
          <div className="px-6 py-10 text-center">
            <p className="text-sm font-semibold">
              {filtered ? 'No parties match' : 'No parties yet'}
            </p>
            <p className="mx-auto mt-1 max-w-md text-sm text-muted-foreground">
              {filtered
                ? 'Clear the filters to see everything.'
                : 'Add the developer you are about to register a project with. Every project references one.'}
            </p>
          </div>
        }
      >
        <Card>
          <CardHeader className="block">
            <DataGridListToolbar
              table={table}
              searchSlot={
                <ListSearchInput
                  value={search}
                  onChange={setSearch}
                  isSettling={isSearchInFlight(debouncedSettling, parties.isFetching, debounced)}
                  placeholder="Search by name…"
                  aria-label="Search parties"
                  className="w-full max-w-xs"
                />
              }
              filters={{
                kind: 'custom',
                active: Boolean(typeFilter),
                activeCount: typeFilter ? 1 : 0,
                content: (
                  <div className="space-y-3">
                    <div className="space-y-1.5">
                      <Label className="text-xs text-muted-foreground">Type</Label>
                      <SearchableSelect
                        value={typeFilter}
                        onChange={setTypeFilter}
                        clearable
                        options={PARTY_TYPE_OPTIONS.map((option) => ({
                          value: option.value,
                          label: option.label,
                        }))}
                        placeholder="All types"
                      />
                    </div>
                    {typeFilter && (
                      <Button
                        variant="outline"
                        size="sm"
                        className="w-full"
                        onClick={() => setTypeFilter('')}
                      >
                        Clear filters
                      </Button>
                    )}
                  </div>
                ),
              }}
              exportConfig={{ filename: 'parties_export.xlsx' }}
              onRefresh={() => void parties.refetch()}
              isRefreshing={parties.isFetching && !parties.isLoading}
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

      {(creating || editing) && (
        <PartyFormDialog
          party={editing}
          onDone={() => {
            setCreating(false);
            setEditing(null);
          }}
        />
      )}

      <ConfirmDeleteDialog
        open={Boolean(deleting)}
        onOpenChange={(next) => !next && setDeleting(null)}
        title="Confirm delete"
        description={
          deleting
            ? `Delete "${deleting.name}"? This action cannot be undone. A party used as a developer on any project cannot be deleted, so deactivate it instead.`
            : ''
        }
        onDelete={async () => {
          if (!deleting) return;
          await remove.mutateAsync(deleting.id);
        }}
        onSuccess={() => setDeleting(null)}
        successMessage="Party deleted"
      />
    </div>
  );
}

function Muted({ children }: { children: React.ReactNode }) {
  return <span className="text-muted-foreground">{children}</span>;
}
