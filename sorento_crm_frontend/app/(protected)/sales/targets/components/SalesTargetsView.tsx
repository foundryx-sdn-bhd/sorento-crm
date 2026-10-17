'use client';

import { useMemo, useState } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import {
  ColumnDef,
  getCoreRowModel,
  getPaginationRowModel,
  useReactTable,
  type PaginationState,
  type RowSelectionState,
} from '@tanstack/react-table';
import { Coins, FileSpreadsheet, Plus, Store, UserRound, UsersRound } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card, CardFooter, CardHeader, CardTable } from '@/components/ui/card';
import { DataGrid } from '@/components/ui/data-grid';
import { DataGridColumnHeader } from '@/components/ui/data-grid-column-header';
import { DataGridListToolbar } from '@/components/ui/data-grid-list-toolbar';
import { DataGridPagination } from '@/components/ui/data-grid-pagination';
import { buildSelectColumn } from '@/components/ui/data-grid-select-column';
import { DataGridTable } from '@/components/ui/data-grid-table';
import { Label } from '@/components/ui/label';
import { Skeleton } from '@/components/ui/skeleton';
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Container } from '@/components/common/container';
import { ListSearchInput } from '@/components/common/ListSearchInput';
import { PageHeader } from '@/components/common/PageHeader';
import { PillOverflow } from '@/components/common/PillOverflow';
import { SearchableSelect } from '@/components/common/SearchableSelect';
import { isSearchInFlight, useDebouncedSearch } from '@/hooks/useDebouncedSearch';
import { useHasPermission } from '@/hooks/usePermissions';
import { useSalesTargets } from '../hooks/useSalesTargets';
import DealerTargetImportDialog from './DealerTargetImportDialog';
import PointRatioDialog from './PointRatioDialog';
import {
  BASIS_LABEL,
  METRIC_LABEL,
  formatFigure,
  formatPct,
  dateRange,
  scopeSummary,
} from '../lib/format';
import type { SalesTargetRow, TargetSubjectKind } from '../types/salesTarget.types';

const TABS: { value: TargetSubjectKind; label: string; icon: typeof UsersRound }[] = [
  { value: 'team', label: 'Teams', icon: UsersRound },
  { value: 'agent', label: 'Agents', icon: UserRound },
  { value: 'dealer', label: 'Dealers', icon: Store },
];

function tabOf(value: string | null): TargetSubjectKind {
  return value === 'agent' || value === 'dealer' ? value : 'team';
}

/** A dealer row's counted accounts, as pills with the rest behind +N. */
function AccountsCell({ row }: { row: SalesTargetRow }) {
  const labels = row.account_labels ?? [];
  if (!labels.length) return <span className="text-muted-foreground">-</span>;
  return (
    <PillOverflow
      ariaLabel={`Accounts ${row.name ?? 'the target'} counts`}
      items={labels.map((label, i) => ({ key: `${i}-${label}`, label }))}
      renderPopover={(all) => (
        <ul className="flex flex-col gap-1 text-sm">
          {all.map((i, index) => (
            <li key={i.key} className="truncate" title={row.account_codes?.[index] ?? undefined}>
              {`${i.label}${row.account_codes?.[index] ? ` (${row.account_codes[index]})` : ''}`}
            </li>
          ))}
        </ul>
      )}
    />
  );
}

/** Keep the tab and team filter in the URL without a navigation (a shareable view). */
function writeUrl(next: Record<string, string>) {
  if (typeof window === 'undefined') return;
  const params = new URLSearchParams(window.location.search);
  for (const [key, value] of Object.entries(next)) {
    if (value) params.set(key, value);
    else params.delete(key);
  }
  const search = params.toString();
  window.history.replaceState(
    window.history.state,
    '',
    `${window.location.pathname}${search ? `?${search}` : ''}`,
  );
}

/** Measure, counts and scope as chips, the measure first (N4). */
function MeasuresCell({ row }: { row: SalesTargetRow }) {
  if (!row.metric) return <span className="text-muted-foreground">-</span>;
  const items = [
    { key: 'metric', label: METRIC_LABEL[row.metric] },
    { key: 'basis', label: row.basis ? BASIS_LABEL[row.basis] : '' },
    { key: 'scope', label: scopeSummary(row.product_scope, row.scope_labels.length) },
  ].filter((i) => i.label);
  return (
    <PillOverflow
      ariaLabel={`What ${row.name ?? 'the target'} counts`}
      items={items}
      renderPopover={(all) => (
        <ul className="flex flex-col gap-1 text-sm">
          {all.map((i) => (
            <li key={i.key}>{i.label}</li>
          ))}
          {row.scope_labels.map((label) => (
            <li key={label} className="truncate text-muted-foreground" title={label}>
              {label}
            </li>
          ))}
        </ul>
      )}
    />
  );
}

function TextCell({ value, muted }: { value: string | null | undefined; muted?: string }) {
  return value ? (
    <span className="block truncate" title={value}>
      {value}
    </span>
  ) : (
    <span className="text-muted-foreground">{muted ?? '-'}</span>
  );
}

/**
 * Sales > Targets (UAC S1-15, S1-18, S1-22; the S1 hand test of 27 Sep, F3).
 *
 * The standard list page: line tabs **Teams** (opening first, N5) and **Agents**, then the
 * list card with the shared `DataGridListToolbar` (search, Columns, Export; on Agents a Filters
 * popover holding the Team filter, "No team" included), the standard header row and pager.
 * Each row is one target of the tab's kind, whatever its dates, with its whole range's target
 * and achievement (`all`): there is no date filter and no "No target" row (owner, 27 Sep: "it
 * should show a list of team target, that's it"). A row opens the target record. Set target
 * opens the new target record for the open tab's kind. The tab and Team filter live in the URL.
 */
export default function SalesTargetsView() {
  const params = useSearchParams();
  const router = useRouter();
  const canAdd = useHasPermission('sales.targets.add');
  const [tab, setTab] = useState<TargetSubjectKind>(tabOf(params.get('tab')));
  const [teamFilter, setTeamFilter] = useState(params.get('team') ?? '');

  const changeTab = (value: string) => {
    const next = tabOf(value);
    setTab(next);
    writeUrl({ tab: next === 'team' ? '' : next, team: next === 'agent' ? teamFilter : '' });
  };
  const changeTeam = (value: string) => {
    setTeamFilter(value);
    writeUrl({ team: value });
  };

  const headerAction = canAdd ? (
    <Button variant="primary" onClick={() => router.push(`/sales/targets/new?kind=${tab}`)}>
      <Plus className="size-4" />
      Set target
    </Button>
  ) : undefined;

  return (
    <>
      <Container>
        <PageHeader title="Targets" actions={headerAction} />
      </Container>
      <Container>
        <div className="space-y-4">
          <Tabs value={tab} onValueChange={changeTab}>
            <TabsList variant="line">
              {TABS.map((t) => (
                <TabsTrigger key={t.value} value={t.value} onClick={() => changeTab(t.value)}>
                  <t.icon className="size-4" />
                  <span>{t.label}</span>
                </TabsTrigger>
              ))}
            </TabsList>
          </Tabs>
          {/* One grid per tab (keyed): the two tabs have different columns, and a shared table
              instance carried the Teams tab's column order into the Agents tab's saved layout. */}
          <TargetsGrid key={tab} tab={tab} teamFilter={teamFilter} onTeamFilter={changeTeam} />
        </div>
      </Container>
    </>
  );
}

/** The list card for one tab: its own query, table state and saved column layout. */
function TargetsGrid({
  tab,
  teamFilter,
  onTeamFilter,
}: {
  tab: TargetSubjectKind;
  teamFilter: string;
  onTeamFilter: (value: string) => void;
}) {
  const {
    value: searchQuery,
    setValue: setSearchQuery,
    debouncedValue: debouncedSearch,
    isSettling,
  } = useDebouncedSearch();
  const [pagination, setPagination] = useState<PaginationState>({ pageIndex: 0, pageSize: 25 });
  const [rowSelection, setRowSelection] = useState<RowSelectionState>({});
  const canEdit = useHasPermission('sales.targets.edit');
  const [importOpen, setImportOpen] = useState(false);
  const [ratioOpen, setRatioOpen] = useState(false);

  const { data, isLoading, isFetching, isPlaceholderData, isError, error } = useSalesTargets({
    all: true,
    subject: tab,
    ...(tab === 'agent' && teamFilter ? { salesTeamId: teamFilter } : {}),
    ...(debouncedSearch ? { query: debouncedSearch } : {}),
  });
  // The Team filter's choices: the teams that have a target (usually already cached).
  const { data: teamList } = useSalesTargets({ all: true, subject: 'team' }, tab === 'agent');
  const rows = useMemo(() => (data?.rows ?? []).filter((r) => r.target_id), [data]);

  const teamOptions = useMemo(() => {
    const seen = new Map<string, string>();
    for (const row of [...(teamList?.rows ?? []), ...rows]) {
      if (row.team_id && row.team_name && !seen.has(row.team_id)) seen.set(row.team_id, row.team_name);
    }
    return [
      { value: 'none', label: 'No team' },
      ...Array.from(seen, ([value, label]) => ({ value, label })).sort((a, b) => a.label.localeCompare(b.label)),
    ];
  }, [teamList, rows]);

  const columns = useMemo<ColumnDef<SalesTargetRow>[]>(() => {
    const subjectColumns: ColumnDef<SalesTargetRow>[] =
      tab === 'dealer'
        ? [
            {
              id: 'dealer',
              header: ({ column }) => <DataGridColumnHeader title="Dealer" column={column} />,
              cell: ({ row }) => <TextCell value={row.original.subject_label} />,
              size: 160,
              meta: { headerTitle: 'Dealer', skeleton: <Skeleton className="h-4 w-28" /> },
            },
            {
              id: 'accounts',
              header: ({ column }) => <DataGridColumnHeader title="Counts" column={column} />,
              cell: ({ row }) => <AccountsCell row={row.original} />,
              size: 150,
              meta: { headerTitle: 'Counts', skeleton: <Skeleton className="h-5 w-24" /> },
            },
            {
              id: 'payout',
              header: ({ column }) => <DataGridColumnHeader title="Pays to" column={column} />,
              cell: ({ row }) => <TextCell value={row.original.payout_label} />,
              size: 100,
              meta: { headerTitle: 'Pays to', skeleton: <Skeleton className="h-4 w-16" /> },
            },
          ]
        : tab === 'team'
        ? [
            {
              id: 'team',
              header: ({ column }) => <DataGridColumnHeader title="Team" column={column} />,
              cell: ({ row }) => <TextCell value={row.original.subject_label} />,
              size: 110,
              meta: { headerTitle: 'Team', skeleton: <Skeleton className="h-4 w-24" /> },
            },
          ]
        : [
            {
              id: 'agent',
              header: ({ column }) => <DataGridColumnHeader title="Agent" column={column} />,
              cell: ({ row }) => <TextCell value={row.original.subject_label} />,
              size: 140,
              meta: { headerTitle: 'Agent', skeleton: <Skeleton className="h-4 w-28" /> },
            },
            {
              id: 'team',
              header: ({ column }) => <DataGridColumnHeader title="Team" column={column} />,
              cell: ({ row }) => <TextCell value={row.original.team_name} muted="No team" />,
              size: 110,
              meta: { headerTitle: 'Team', skeleton: <Skeleton className="h-4 w-20" /> },
            },
          ];
    return [
      buildSelectColumn<SalesTargetRow>(),
      {
        id: 'target_no',
        header: ({ column }) => <DataGridColumnHeader title="Number" column={column} />,
        cell: ({ row }) => <TextCell value={row.original.target_no} />,
        size: 130,
        meta: { headerTitle: 'Number', skeleton: <Skeleton className="h-4 w-20" /> },
      },
      {
        id: 'name',
        header: ({ column }) => <DataGridColumnHeader title="Name" column={column} />,
        cell: ({ row }) => (
          <span className="block truncate font-medium" title={row.original.name ?? undefined}>
            {row.original.name}
          </span>
        ),
        size: 140,
        meta: { headerTitle: 'Name', skeleton: <Skeleton className="h-4 w-32" /> },
      },
      ...subjectColumns,
      {
        id: 'measures',
        header: ({ column }) => <DataGridColumnHeader title="Measures" column={column} />,
        cell: ({ row }) => <MeasuresCell row={row.original} />,
        size: 125,
        meta: { headerTitle: 'Measures', skeleton: <Skeleton className="h-5 w-24" /> },
      },
      {
        id: 'dates',
        header: ({ column }) => <DataGridColumnHeader title="Dates" column={column} />,
        cell: ({ row }) => (
          <TextCell value={dateRange(row.original.start_date, row.original.end_date)} />
        ),
        size: 170,
        meta: { headerTitle: 'Dates', skeleton: <Skeleton className="h-4 w-32" /> },
      },
      {
        id: 'target_value',
        header: ({ column }) => <DataGridColumnHeader title="Target" column={column} />,
        cell: ({ row }) => (
          <span className="block truncate text-end tabular-nums">{formatFigure(row.original.target_value)}</span>
        ),
        size: 85,
        meta: { headerTitle: 'Target', skeleton: <Skeleton className="h-4 w-16" /> },
      },
      {
        id: 'achieved',
        header: ({ column }) => (
          <DataGridColumnHeader
            // A dealer's sales are the sales orders' totals until the invoice sync (ruling 1).
            title={tab === 'dealer' ? 'Achieved (provisional, incl. tax)' : 'Achieved'}
            column={column}
          />
        ),
        cell: ({ row }) => (
          <span className="block truncate text-end tabular-nums">{formatFigure(row.original.achieved_value)}</span>
        ),
        size: tab === 'dealer' ? 130 : 85,
        meta: { headerTitle: 'Achieved', skeleton: <Skeleton className="h-4 w-16" /> },
      },
      {
        id: 'pct',
        header: ({ column }) => <DataGridColumnHeader title="%" column={column} />,
        cell: ({ row }) => {
          const pct = row.original.achieved_pct;
          return (
            <span
              className={
                pct !== null && pct >= 100
                  ? 'block text-end font-medium tabular-nums text-success'
                  : 'block text-end tabular-nums'
              }
            >
              {formatPct(pct)}
            </span>
          );
        },
        size: 60,
        meta: { headerTitle: '%', skeleton: <Skeleton className="h-4 w-10" /> },
      },
      ...(tab === 'dealer'
        ? [
            {
              id: 'commission',
              header: ({ column }) => <DataGridColumnHeader title="Commission" column={column} />,
              cell: ({ row }) => (
                <span className="block truncate text-end tabular-nums">
                  {formatFigure(row.original.commission_earned ?? null)}
                </span>
              ),
              size: 100,
              meta: { headerTitle: 'Commission', skeleton: <Skeleton className="h-4 w-16" /> },
            } satisfies ColumnDef<SalesTargetRow>,
          ]
        : []),
    ];
  }, [tab]);

  const table = useReactTable({
    columns,
    data: rows,
    getRowId: (row) => row.target_id as string,
    state: { pagination, rowSelection },
    enableRowSelection: true,
    onRowSelectionChange: setRowSelection,
    onPaginationChange: setPagination,
    getCoreRowModel: getCoreRowModel(),
    getPaginationRowModel: getPaginationRowModel(),
    // The server orders the targets: newest dates first, then by who and number.
    enableSorting: false,
    columnResizeMode: 'onChange',
    enableColumnResizing: true,
  });

  const filterActive = tab === 'agent' && !!teamFilter;
  const emptyMessage = debouncedSearch
    ? 'Nothing matches this search.'
    : tab === 'team'
      ? 'No team targets yet'
      : tab === 'dealer'
        ? 'No dealer targets yet'
        : 'No agent targets yet';

  return (
    <DataGrid
      table={table}
      recordCount={rows.length}
      isLoading={isLoading}
      isPlaceholderData={isPlaceholderData}
      listingKey={`sales.targets.view::${tab}`}
      standardToolbar={false}
      tableLayout={{ width: 'fixed', columnsResizable: true, columnsVisibility: true }}
      emptyMessage={emptyMessage}
      rowHref={(row) => `/sales/targets/${row.target_id}`}
    >
      <Card>
        <CardHeader className="block">
          <DataGridListToolbar
            table={table}
            searchSlot={
              <ListSearchInput
                value={searchQuery}
                onChange={setSearchQuery}
                isSettling={isSearchInFlight(isSettling, isFetching, debouncedSearch)}
                placeholder="Search targets..."
                className="w-full sm:w-64"
              />
            }
            filters={
              tab === 'agent'
                ? {
                    kind: 'custom',
                    active: filterActive,
                    activeCount: filterActive ? 1 : 0,
                    content: (
                      <div className="flex w-64 flex-col gap-1.5">
                        <Label htmlFor="targets-team-filter">Team</Label>
                        <SearchableSelect
                          id="targets-team-filter"
                          value={teamFilter}
                          onChange={onTeamFilter}
                          options={teamOptions}
                          placeholder="All teams"
                          clearable
                        />
                      </div>
                    ),
                  }
                : undefined
            }
            exportConfig={{ filename: `${tab}_targets.xlsx` }}
            secondaryActions={
              tab === 'dealer' && canEdit
                ? [
                    { key: 'import', label: 'Import from Excel', icon: FileSpreadsheet, onClick: () => setImportOpen(true) },
                    { key: 'ratio', label: 'Point ratio', icon: Coins, onClick: () => setRatioOpen(true) },
                  ]
                : undefined
            }
          />
        </CardHeader>
        {isError ? (
          <div className="px-5 pb-2 text-sm text-destructive">
            {error instanceof Error ? error.message : 'Failed to load targets.'}
          </div>
        ) : null}
        <CardTable>
          <DataGridTable />
        </CardTable>
        <CardFooter>
          <DataGridPagination />
        </CardFooter>
      </Card>
      {tab === 'dealer' && canEdit ? (
        <>
          <DealerTargetImportDialog open={importOpen} onOpenChange={setImportOpen} />
          <PointRatioDialog open={ratioOpen} onOpenChange={setRatioOpen} />
        </>
      ) : null}
    </DataGrid>
  );
}
