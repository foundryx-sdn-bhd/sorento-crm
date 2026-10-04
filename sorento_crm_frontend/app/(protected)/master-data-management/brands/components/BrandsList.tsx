'use client';

import { useMemo, useState } from 'react';
import {
  RowSelectionState,
  getCoreRowModel,
  useReactTable,
} from '@tanstack/react-table';
import { Plus } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Label } from '@/components/ui/label';
import { useDebouncedSearch } from '@/hooks/useDebouncedSearch';
import { ListSearchInput } from '@/components/common/ListSearchInput';
import { Card, CardHeader, CardTable } from '@/components/ui/card';
import { SearchableSelect } from '@/components/common/SearchableSelect';
import { DataGrid } from '@/components/ui/data-grid';
import { DataGridTable } from '@/components/ui/data-grid-table';
import { DataGridListToolbar } from '@/components/ui/data-grid-list-toolbar';
import { useBrands } from '../hooks/useBrands';
import { buildBrandColumns } from './BrandTable';
import BrandFormDialog from './BrandFormDialog';
import {
  useDeferredRowAction,
  useRowPending,
} from '@/hooks/useDeferredRowAction';
import type { Brand } from '../types/brand.types';

export default function BrandsList() {
  const {
    value: searchInput,
    setValue: setSearchInput,
    debouncedValue: searchQuery,
  } = useDebouncedSearch();
  const [statusFilter, setStatusFilter] = useState<'all' | 'active' | 'inactive'>('all');
  const [rowSelection, setRowSelection] = useState<RowSelectionState>({});
  const [formOpen, setFormOpen] = useState(false);
  const [editingBrandId, setEditingBrandId] = useState<string | undefined>(undefined);
  const [copyFromBrand, setCopyFromBrand] = useState<Brand | null>(null);
  // Delete asks nothing (D7): the row dims and a toast counts down with Cancel.
  const deletion = useDeferredRowAction({
    actionKey: 'brand.delete',
    entityType: 'brand',
    successMessage: 'Brand deleted',
    // The select feeds every form that picks a brand, so it is refetched beside the
    // list - the immediate mutation always did both, and a deferred delete that
    // forgot one leaves a deleted brand pickable until the next hard refresh.
    invalidateKeys: [['brands'], ['brand-select']],
  });
  const rowPending = useRowPending<Brand>('brand');

  const { data, isLoading, isPlaceholderData, error, refetch } = useBrands({
    pageIndex: 0,
    pageSize: 100,
    sorting: [{ id: 'brand_name', desc: false }],
    searchQuery: '',
  });
  const handleDuplicate = (brand: Brand) => {
    setEditingBrandId(undefined);
    setCopyFromBrand(brand);
    setFormOpen(true);
  };

  const handleDelete = (brand: Brand) => {
    deletion.run({ id: brand.id, subject: `${brand.brand_name} (${brand.brand_code})` });
  };

  const handleFormClose = (open: boolean) => {
    setFormOpen(open);
    if (!open) {
      setEditingBrandId(undefined);
      setCopyFromBrand(null);
    }
  };

  // Search + status are filtered client-side (the list loads up to 100 brands).
  const filteredBrands = useMemo(() => {
    const brands = data?.data ?? [];
    const q = searchQuery.toLowerCase();
    return brands.filter((b) => {
      const matchesSearch =
        !q ||
        b.brand_name?.toLowerCase().includes(q) ||
        b.brand_code?.toLowerCase().includes(q);
      const matchesStatus =
        statusFilter === 'all' ||
        (statusFilter === 'active' ? b.is_active : !b.is_active);
      return matchesSearch && matchesStatus;
    });
  }, [data, searchQuery, statusFilter]);

  const columns = useMemo(
    () => buildBrandColumns({ onDuplicate: handleDuplicate, onDelete: handleDelete }),
    [],
  );

  const table = useReactTable({
    columns,
    data: filteredBrands,
    getRowId: (row) => row.id,
    state: { rowSelection },
    enableRowSelection: true,
    onRowSelectionChange: setRowSelection,
    getCoreRowModel: getCoreRowModel(),
  });

  const statusActive = statusFilter !== 'all';

  // The one offer this listing makes, in both places it belongs: the
  // toolbar, and the empty state's next step (S5-06).
  const listPrimaryAction = (
    <Button
      onClick={() => {
        setCopyFromBrand(null);
        setEditingBrandId(undefined);
        setFormOpen(true);
      }}
    >
      <Plus className="size-4" />
      Create Brand
    </Button>
  );

  return (
    <>
      {/* The row opens the brand's record, where view and edit are the same
          layout (ADR product standards). Add stays a lightbox - a brand is five
          fields and does not need a page of its own to be created. */}
      <DataGrid
        table={table}
        recordCount={filteredBrands.length}
        isLoading={isLoading}
        error={error}
        onRetry={() => void refetch()}
        isPlaceholderData={isPlaceholderData}
        rowHref={(row) => `/master-data-management/brands/${row.id}`}
        rowPending={rowPending}
        tableLayout={{ width: 'fixed', columnsResizable: true, columnsVisibility: true }}
      >
        <Card>
          <CardHeader className="block">
            <DataGridListToolbar
              table={table}
              searchSlot={
                <ListSearchInput
                  value={searchInput}
                  onChange={setSearchInput}
                  placeholder="Search brands..."
                  className="w-64"
                />
              }
              filters={{
                kind: 'custom',
                active: statusActive,
                activeCount: statusActive ? 1 : 0,
                content: (
                  <div className="space-y-4">
                    <div>
                      <Label>Status</Label>
                      <SearchableSelect
                        value={statusFilter}
                        onChange={(v) => setStatusFilter(v as 'all' | 'active' | 'inactive')}
                        placeholder="All statuses"
                        triggerClassName="mt-1"
                        options={[
                          { value: 'all', label: 'All statuses' },
                          { value: 'active', label: 'Active' },
                          { value: 'inactive', label: 'Inactive' },
                        ]}
                      />
                    </div>
                    {statusActive && (
                      <div className="flex justify-end">
                        <Button variant="ghost" size="sm" onClick={() => setStatusFilter('all')}>
                          Clear filters
                        </Button>
                      </div>
                    )}
                  </div>
                ),
              }}
              exportConfig={{ filename: 'brands_export.xlsx' }}
              primaryAction={listPrimaryAction}
            />
          </CardHeader>
          <CardTable>
            <DataGridTable />
          </CardTable>
        </Card>
      </DataGrid>

      <BrandFormDialog
        open={formOpen}
        onOpenChange={handleFormClose}
        brandId={editingBrandId}
        copyFromBrand={copyFromBrand}
      />
    </>
  );
}
