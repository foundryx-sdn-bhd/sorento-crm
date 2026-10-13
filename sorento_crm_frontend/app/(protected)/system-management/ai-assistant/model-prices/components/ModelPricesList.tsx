'use client';

import { useMemo, useState } from 'react';
import { getCoreRowModel, useReactTable, type ColumnDef } from '@tanstack/react-table';
import { Pencil, Plus, Trash2 } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card, CardHeader, CardTable, CardTitle } from '@/components/ui/card';
import { DataGrid } from '@/components/ui/data-grid';
import { DataGridColumnHeader } from '@/components/ui/data-grid-column-header';
import { DataGridTable } from '@/components/ui/data-grid-table';
import { DataGridListToolbar } from '@/components/ui/data-grid-list-toolbar';
import { ListSearchInput } from '@/components/common/ListSearchInput';
import { useDebouncedSearch } from '@/hooks/useDebouncedSearch';
import { useHasPermission } from '@/hooks/usePermissions';
import { formatDateTimeInMalaysia } from '@/lib/helpers';
import { useModelPriceDeletion, useModelPricesQuery } from '../../hooks/useModelPrices';
import type { ModelPrice } from '../../services/modelPriceService';
import { formatPrice } from '../../lib/formatUsd';
import ModelPriceModal from './ModelPriceModal';

const EDIT_PERMISSION = 'system.ai_assistant_settings.edit';

export default function ModelPricesList() {
  const {
    value: searchInput,
    setValue: setSearchInput,
    debouncedValue: searchQuery,
  } = useDebouncedSearch();
  const [modalOpen, setModalOpen] = useState(false);
  const [editing, setEditing] = useState<ModelPrice | null>(null);

  const canEdit = useHasPermission(EDIT_PERMISSION);
  const { data, isLoading, isError, error } = useModelPricesQuery();
  const deletion = useModelPriceDeletion();
  const rows = useMemo(() => data ?? [], [data]);

  const filtered = useMemo(() => {
    const q = searchQuery.trim().toLowerCase();
    return q ? rows.filter((r) => r.model.toLowerCase().includes(q)) : rows;
  }, [rows, searchQuery]);

  const columns = useMemo<ColumnDef<ModelPrice>[]>(() => {
    const priceColumn = (
      key: 'input_usd_per_1m' | 'cached_input_usd_per_1m' | 'output_usd_per_1m',
      title: string,
    ): ColumnDef<ModelPrice> => ({
      accessorKey: key,
      header: ({ column }) => <DataGridColumnHeader title={title} column={column} />,
      cell: ({ row }) => (
        <span className="block text-right tabular-nums">{formatPrice(row.original[key])}</span>
      ),
      size: 120,
      meta: { headerTitle: title },
    });
    const cols: ColumnDef<ModelPrice>[] = [
      {
        accessorKey: 'model',
        header: ({ column }) => <DataGridColumnHeader title="Model" column={column} />,
        cell: ({ row }) => (
          <span className="block truncate font-medium" title={row.original.model}>
            {row.original.model}
          </span>
        ),
        size: 220,
        meta: { headerTitle: 'Model' },
      },
      priceColumn('input_usd_per_1m', 'Input'),
      priceColumn('cached_input_usd_per_1m', 'Cached input'),
      priceColumn('output_usd_per_1m', 'Output'),
      {
        accessorKey: 'note',
        header: ({ column }) => <DataGridColumnHeader title="Note" column={column} />,
        cell: ({ row }) => (
          <span
            className="block truncate text-muted-foreground"
            title={row.original.note ?? undefined}
          >
            {row.original.note ?? ''}
          </span>
        ),
        size: 220,
        meta: { headerTitle: 'Note' },
      },
      {
        accessorKey: 'updated_at',
        header: ({ column }) => <DataGridColumnHeader title="Updated" column={column} />,
        cell: ({ row }) => formatDateTimeInMalaysia(row.original.updated_at),
        size: 160,
        meta: { headerTitle: 'Updated' },
      },
    ];
    if (canEdit) {
      cols.push({
        id: 'actions',
        header: '',
        cell: ({ row }) => (
          <div className="flex items-center justify-end gap-1">
            <Button
              mode="icon"
              variant="ghost"
              aria-label={`Edit ${row.original.model}`}
              onClick={() => {
                setEditing(row.original);
                setModalOpen(true);
              }}
            >
              <Pencil className="size-4" />
            </Button>
            <Button
              mode="icon"
              variant="ghost"
              aria-label={`Delete ${row.original.model}`}
              disabled={deletion.isRowPending(row.original.id)}
              onClick={() => deletion.run({ id: row.original.id, subject: row.original.model })}
            >
              <Trash2 className="size-4" />
            </Button>
          </div>
        ),
        size: 90,
        enableHiding: false,
      });
    }
    return cols;
  }, [canEdit, deletion]);

  const table = useReactTable({
    data: filtered,
    columns,
    getRowId: (row) => row.id,
    getCoreRowModel: getCoreRowModel(),
    columnResizeMode: 'onChange',
  });

  const openCreate = () => {
    setEditing(null);
    setModalOpen(true);
  };

  if (isError) {
    return (
      <Card className="min-w-0 max-w-full">
        <CardHeader>
          <CardTitle>Priced models</CardTitle>
        </CardHeader>
        <p className="px-5 pb-5 text-sm text-destructive">
          {(error as Error | null)?.message || 'Model prices could not be loaded.'}
        </p>
      </Card>
    );
  }

  return (
    <>
      <DataGrid
        table={table}
        recordCount={filtered.length}
        isLoading={isLoading}
        tableLayout={{ width: 'fixed', columnsResizable: true }}
        emptyMessage={
          <div className="py-6 text-center">
            <p className="text-sm font-medium">No prices yet</p>
            <p className="text-xs text-muted-foreground">Add a price to cost this model&apos;s calls.</p>
          </div>
        }
      >
        <Card className="min-w-0 max-w-full">
          <CardHeader className="block">
            <DataGridListToolbar
              table={table}
              showColumns={false}
              exportConfig={false}
              searchSlot={
                <ListSearchInput
                  value={searchInput}
                  onChange={setSearchInput}
                  placeholder="Search models"
                  className="w-full sm:w-72"
                />
              }
              primaryAction={
                canEdit ? (
                  <Button onClick={openCreate}>
                    <Plus className="size-4" />
                    Add price
                  </Button>
                ) : undefined
              }
            />
          </CardHeader>
          <CardTable>
            <DataGridTable />
          </CardTable>
        </Card>
      </DataGrid>

      <ModelPriceModal open={modalOpen} onOpenChange={setModalOpen} price={editing} />
    </>
  );
}
