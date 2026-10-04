'use client';

import { useEffect, useMemo, useState } from 'react';
import {
  ColumnDef,
  PaginationState,
  SortingState,
  getCoreRowModel,
  useReactTable,
} from '@tanstack/react-table';
import { Trash2 } from 'lucide-react';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import {
  Card,
  CardFooter,
  CardHeader,
  CardHeading,
  CardTable,
} from '@/components/ui/card';
import { DataGrid } from '@/components/ui/data-grid';
import { DataGridColumnHeader } from '@/components/ui/data-grid-column-header';
import { DataGridPagination } from '@/components/ui/data-grid-pagination';
import { DataGridTable } from '@/components/ui/data-grid-table';
import { Input } from '@/components/ui/input';
import { SearchableSelect } from '@/components/common/SearchableSelect';
import { Skeleton } from '@/components/ui/skeleton';

import { useDeferredRowAction, useRowPending } from '@/hooks/useDeferredRowAction';
import { isSearchInFlight, useDebouncedSearch } from '@/hooks/useDebouncedSearch';
import { ListSearchInput } from '@/components/common/ListSearchInput';
import { formatDateTimeInMalaysia } from '@/lib/helpers';

import { useTranslations, useUpdateTranslation } from '../hooks/useTranslations';
import type { Translation } from '../types/translation.types';

/**
 * A translation's target cell, editable in place: type, blur (or Enter) saves.
 * Editing here writes `source: 'manual'` server-side regardless of what it was
 * before (R16) - the badge on the same row reflects that the moment the save lands,
 * it does not wait for a full refetch.
 */
function TargetTextCell({
  row,
  onSave,
  disabled,
}: {
  row: Translation;
  onSave: (targetText: string) => void;
  disabled: boolean;
}) {
  const [value, setValue] = useState(row.target_text);

  useEffect(() => {
    setValue(row.target_text);
  }, [row.target_text]);

  const commit = () => {
    const trimmed = value.trim();
    if (!trimmed || trimmed === row.target_text) {
      setValue(row.target_text);
      return;
    }
    onSave(trimmed);
  };

  return (
    <Input
      value={value}
      onChange={(e) => setValue(e.target.value)}
      onBlur={commit}
      onKeyDown={(e) => {
        if (e.key === 'Enter') (e.target as HTMLInputElement).blur();
      }}
      disabled={disabled}
      aria-label={`Translation for ${row.source_text}`}
      className="h-8"
    />
  );
}

const LANGUAGE_NAMES: Record<string, string> = { en: 'English', ms: 'Malay', zh: 'Chinese' };

/** The supplier memory reads Chinese into English; the chatbot wording reads English into
 * Malay or Chinese. The filter is by the language a row translates into. */
const LANGUAGE_OPTIONS = [
  { value: 'en', label: 'Into English' },
  { value: 'ms', label: 'Into Malay' },
  { value: 'zh', label: 'Into Chinese' },
];

function languagePair(row: Translation): string {
  const name = (code: string) => LANGUAGE_NAMES[code] ?? code;
  return `${name(row.source_lang)} to ${name(row.target_lang)}`;
}

export default function TranslationsList() {
  const [pagination, setPagination] = useState<PaginationState>({ pageIndex: 0, pageSize: 25 });
  const [sorting, setSorting] = useState<SortingState>([]);
  const [targetLang, setTargetLang] = useState('');
  const {
    value: searchQuery,
    setValue: setSearchQuery,
    debouncedValue: debouncedSearch,
    isSettling: debouncedSearchSettling,
  } = useDebouncedSearch();

  useEffect(() => {
    setPagination((p) => ({ ...p, pageIndex: 0 }));
  }, [debouncedSearch, sorting, targetLang]);

  const { data, isLoading, isFetching, isPlaceholderData, isError, error } = useTranslations({
    pageIndex: pagination.pageIndex,
    pageSize: pagination.pageSize,
    sorting,
    searchQuery: debouncedSearch,
    targetLang,
  });
  const updateTranslation = useUpdateTranslation();
  // Delete asks nothing (D7): the row dims and a toast counts down with Cancel.
  const deletion = useDeferredRowAction({
    actionKey: 'translation_memory.delete',
    entityType: 'translation_memory',
    successMessage: 'Translation deleted',
    invalidateKeys: [['translations']],
  });
  const rowPending = useRowPending<Translation>('translation_memory');

  const rows = useMemo<Translation[]>(() => data?.data ?? [], [data]);
  const total = data?.pagination?.total ?? 0;

  const columns = useMemo<ColumnDef<Translation>[]>(
    () => [
      {
        accessorKey: 'source_text',
        header: ({ column }) => <DataGridColumnHeader title="Source" column={column} />,
        cell: ({ row }) => (
          <span className="block truncate" title={row.original.source_text}>
            {row.original.source_text}
          </span>
        ),
        size: 260,
        meta: { headerTitle: 'Source', skeleton: <Skeleton className="h-5 w-40" /> },
      },
      {
        id: 'language',
        header: ({ column }) => <DataGridColumnHeader title="Language" column={column} />,
        cell: ({ row }) => (
          <span className="block truncate text-muted-foreground" title={languagePair(row.original)}>
            {languagePair(row.original)}
          </span>
        ),
        enableSorting: false,
        size: 150,
        meta: { headerTitle: 'Language' },
      },
      {
        accessorKey: 'target_text',
        header: ({ column }) => <DataGridColumnHeader title="Translation" column={column} />,
        cell: ({ row }) => (
          <TargetTextCell
            row={row.original}
            disabled={updateTranslation.isPending}
            onSave={(target_text) =>
              updateTranslation.mutate({ id: row.original.id, body: { target_text } })
            }
          />
        ),
        enableSorting: false,
        size: 260,
        meta: { headerTitle: 'Translation' },
      },
      {
        accessorKey: 'source',
        header: ({ column }) => <DataGridColumnHeader title="Source kind" column={column} />,
        cell: ({ row }) =>
          row.original.source === 'manual' ? (
            <Badge variant="primary" appearance="light" size="sm">
              manual
            </Badge>
          ) : (
            <Badge variant="secondary" appearance="light" size="sm">
              ai
            </Badge>
          ),
        size: 110,
        meta: { headerTitle: 'Source kind' },
      },
      {
        accessorKey: 'created_by_name',
        header: ({ column }) => <DataGridColumnHeader title="By" column={column} />,
        cell: ({ row }) => (
          <span className="truncate text-muted-foreground" title={row.original.created_by_name ?? undefined}>
            {row.original.created_by_name || '-'}
          </span>
        ),
        enableSorting: false,
        size: 140,
        meta: { headerTitle: 'By' },
      },
      {
        accessorKey: 'hit_count',
        header: ({ column }) => <DataGridColumnHeader title="Hits" column={column} />,
        cell: ({ row }) => <span className="tabular-nums">{row.original.hit_count}</span>,
        size: 80,
        meta: { headerTitle: 'Hits', headerClassName: 'text-end', cellClassName: 'text-end' },
      },
      {
        accessorKey: 'updated_at',
        header: ({ column }) => <DataGridColumnHeader title="Updated" column={column} />,
        cell: ({ row }) => (
          <span className="text-muted-foreground">
            {formatDateTimeInMalaysia(row.original.updated_at)}
          </span>
        ),
        size: 170,
        meta: { headerTitle: 'Updated' },
      },
      {
        id: 'actions',
        header: () => <span className="sr-only">Actions</span>,
        cell: ({ row }) => (
          <div className="flex justify-end">
            <Button
              mode="icon"
              variant="ghost"
              size="sm"
              aria-label={`Delete translation of ${row.original.source_text}`}
              title="Delete translation"
              onClick={() =>
                deletion.run({ id: row.original.id, subject: row.original.source_text })
              }
            >
              <Trash2 className="size-4 text-destructive" />
            </Button>
          </div>
        ),
        size: 70,
        enableSorting: false,
        meta: { headerTitle: 'Actions', cellClassName: 'text-right' },
      },
    ],
    [deletion, updateTranslation],
  );

  const table = useReactTable({
    columns,
    data: rows,
    pageCount: Math.max(1, Math.ceil(total / pagination.pageSize)),
    rowCount: total,
    getRowId: (row) => row.id,
    state: { pagination, sorting },
    onPaginationChange: setPagination,
    onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(),
    manualPagination: true,
    manualSorting: true,
    manualFiltering: true,
    columnResizeMode: 'onChange',
    enableColumnResizing: true,
  });

  return (
    <div className="space-y-3">
      {isError ? (
        <div
          className="rounded-lg border border-destructive/40 bg-destructive/5 p-3 text-sm text-destructive"
          data-testid="translations-error"
        >
          {error instanceof Error ? error.message : 'Failed to load translations.'}
        </div>
      ) : null}

      <DataGrid
        table={table}
        recordCount={total}
        isLoading={isLoading}
        isPlaceholderData={isPlaceholderData}
        tableLayout={{ width: 'fixed', columnsResizable: true }}
        rowPending={rowPending}
        emptyMessage="No translations yet. Upload a supplier document to add one."
      >
        <Card>
          <CardHeader className="flex items-center justify-between gap-3">
            <CardHeading className="flex w-full flex-col gap-2 sm:flex-row sm:items-center">
              <ListSearchInput
                value={searchQuery}
                onChange={setSearchQuery}
                isSettling={isSearchInFlight(debouncedSearchSettling, isFetching, debouncedSearch)}
                placeholder="Search translations..."
                aria-label="Search translations"
                className="w-full sm:w-64"
              />
              <div className="w-full sm:w-48" data-testid="translations-language-filter">
                <SearchableSelect
                  value={targetLang}
                  onChange={setTargetLang}
                  options={LANGUAGE_OPTIONS}
                  placeholder="All languages"
                  clearable
                  size="sm"
                />
              </div>
            </CardHeading>
          </CardHeader>

          <CardTable>
            <DataGridTable />
          </CardTable>
          <CardFooter>
            <DataGridPagination />
          </CardFooter>
        </Card>
      </DataGrid>
    </div>
  );
}
